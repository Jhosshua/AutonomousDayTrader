"""ORBStraddle tests/test_news_retry_2026_10_02.py ported to ADT's copy (ORBStraddle @05d370d, 11 tests).

2026-10-02: nine ORB robots scan one shared relay at the same minutes, so a single news page can fail for
a moment. A transient page failure must retry inside the fetch budget, a real failure must still fail
closed, the ledger must say why, and each robot's scanner must run few enough threads that nine of them
fit the relay's admission bound.

Differences from the original test: HTTP is patched at the ADT shim (`shim.urlopen`, what the copied
`_relay_page` calls as `core.urlopen`) instead of `adaptive.urllib.request.urlopen`; the ledger is the
shim's `_ledger`; the relay URLs come from `config.set_relay` (ORBStraddle reads them from its env).
No network anywhere.
"""
import json
import urllib.error
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

from backend.app.strategies.orbs import adaptive, config, scanner
from backend.app.strategies.orbs import shim as core

FREEZE = datetime(2026, 10, 2, 9, 38, tzinfo=core.ET)


@pytest.fixture(autouse=True)
def relay_urls():
    """The copied code builds request URLs from config; an unconfigured copy has empty ones."""
    saved = (config.RELAY_DATA, config.RELAY_META, config.RELAY_TOKEN, config.RELAY_UA, dict(config.RELAY_HEADERS),
             config.NEWS_URL, config.INDEX_BARS_URL)
    config.set_relay("https://relay.test", "test-token")
    try:
        yield
    finally:
        (config.RELAY_DATA, config.RELAY_META, config.RELAY_TOKEN, config.RELAY_UA, config.RELAY_HEADERS,
         config.NEWS_URL, config.INDEX_BARS_URL) = saved


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self._payload


def _card(symbol, direction):
    return {"symbol": symbol, "direction": direction, "entry": 100.0, "stop": 96.0,
            "gap_pct": 1.5, "drift_pct": 0.2, "rvol": 3.0, "atr_pct": 3.0,
            "spread_bps": 4.0, "sofi_z": 2.0, "trig": "09:35:00"}


def _busy(code, retry_after=None):
    headers = {"Retry-After": str(retry_after)} if retry_after is not None else {}
    return urllib.error.HTTPError("https://relay.test/data/v1beta1/news", code, "busy", headers, None)


class _Clock:
    """Fake monotonic clock: only sleeping moves it, so budget arithmetic is exact."""
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def test_the_copy_routes_the_retried_page_through_the_shim():
    """The generated helper must call the shim (so replay transports and these tests see it), never urllib."""
    with open(adaptive.__file__, encoding="utf-8") as handle:
        src = handle.read()
    assert "urllib.request.urlopen(" not in src
    assert "core.urlopen(req, timeout=timeout)" in src
    assert hasattr(core, "_ledger") and hasattr(core, "urlopen")


def test_relay_busy_page_is_retried_and_the_fetch_succeeds():
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=[_busy(503, retry_after=1), _Response({"news": []})]) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health") as health:
        assert adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=30) == {}
    assert opened.call_count == 2
    assert clock.slept == [1.0]            # the relay's Retry-After is honoured
    assert health.call_args.args[:3] == ("news", "Company news", "ok")


def test_three_transport_failures_fail_closed_with_the_cause_on_the_error():
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=ConnectionResetError("peer reset")) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health") as health:
        try:
            adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=30)
        except adaptive.NewsDataError as exc:
            assert exc.detail["attempts"] == 3
            assert exc.detail["cause"].startswith("ConnectionResetError: peer reset")
        else:
            raise AssertionError("a persistent transport failure must fail closed")
    assert opened.call_count == 3
    assert clock.slept == [0.5, 1.0]
    assert health.call_args.args[:3] == ("news", "Company news", "error")
    assert "peer reset" not in str(health.call_args)   # telemetry stays free of raw detail


def test_client_errors_are_not_retried():
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=_busy(401)) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"):
        try:
            adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=30)
        except adaptive.NewsDataError as exc:
            assert exc.detail["attempts"] == 1
        else:
            raise AssertionError("a 401 must fail closed at once")
    assert opened.call_count == 1
    assert clock.slept == []


def test_retries_never_sleep_past_the_budget():
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=OSError("down")) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"):
        try:
            adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=0.6)
        except adaptive.NewsDataError as exc:
            assert exc.detail["attempts"] == 2   # the third try would need a 1.0 s pause; 0.1 s is left
        else:
            raise AssertionError("must fail closed")
    assert opened.call_count == 2
    assert clock.slept == [0.5]
    assert abs(opened.call_args_list[1].kwargs["timeout"] - 0.1) < 1e-9   # the last try is cut to what is left


def test_news_failure_is_written_to_the_ledger_with_its_cause():
    err = adaptive.NewsDataError("news provider request failed")
    err.detail = {"attempts": 3, "cause": "OSError: down"}
    rows = []
    with patch.object(adaptive, "fetch_earnings_headlines", side_effect=err), \
         patch.object(adaptive, "_record_health"), \
         patch.object(core, "_ledger", rows.append):
        picks, audit, regime = adaptive.select_adaptive([_card("AAA", "long"), _card("BBB", "short")], day="2026-10-02",
                                                         spy_slope=0.0, qqq_slope=0.0)
    assert picks == [] and regime["action"] == "SIT_OUT_CASH"
    assert rows == [{"kind": "news_unavailable", "day": "2026-10-02",
                     "why": "news provider request failed",
                     "detail": {"attempts": 3, "cause": "OSError: down"}}]


def test_the_shim_ledger_accepts_the_new_rows():
    """The real shim `_ledger` (not a patch): the row lands in LEDGER_ROWS with ts and rules_version."""
    err = adaptive.NewsDataError("news provider request failed")
    err.detail = {"attempts": 3, "cause": "OSError: down"}
    core.LEDGER_ROWS.clear()
    with patch.object(adaptive, "fetch_earnings_headlines", side_effect=err), \
         patch.object(adaptive, "_record_health"):
        picks, _audit, regime = adaptive.select_adaptive([_card("AAA", "long"), _card("BBB", "short")],
                                                          day="2026-10-02", spy_slope=0.0, qqq_slope=0.0)
    assert picks == [] and regime["action"] == "SIT_OUT_CASH"
    rows = [r for r in core.LEDGER_ROWS if r.get("kind") == "news_unavailable"]
    assert len(rows) == 1
    assert rows[0]["detail"] == {"attempts": 3, "cause": "OSError: down"}
    assert rows[0]["rules_version"] == config.RULES_VERSION == "adaptive-v1.7.1-deal-rule"
    assert rows[0]["ts"]


def test_a_ledger_write_failure_does_not_change_the_cash_pass():
    def boom(_row):
        raise OSError("disk full")
    with patch.object(adaptive, "fetch_earnings_headlines", side_effect=adaptive.NewsDataError()), \
         patch.object(adaptive, "_record_health"), \
         patch.object(core, "_ledger", boom):
        picks, audit, regime = adaptive.select_adaptive([_card("AAA", "long"), _card("BBB", "short")], day="2026-10-02",
                                                         spy_slope=0.0, qqq_slope=0.0)
    assert picks == [] and regime["action"] == "SIT_OUT_CASH" and audit[0]["tier"] == "news"


def test_each_robot_scans_with_eight_threads():
    captured = {}

    class _Session:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

        def fingerprint(self):
            return "fp"

    d = date(2026, 10, 2)
    with patch.object(scanner.orbproc, "Session", _Session), \
         patch.object(scanner, "_SESSION", None), patch.object(scanner, "_SESSION_KEY", None), \
         patch.dict(scanner._prep, {"wl": ["AAA"], "prev_dv": {}, "atr": {}}), \
         patch.object(scanner, "get_relay_client", lambda: type("C", (), {"base_url": "https://relay"})()):
        scanner._get_session(d)
    assert scanner.SCAN_CONCURRENCY == 8
    assert captured["concurrency"] == scanner.SCAN_CONCURRENCY


def _index_bars():
    start = datetime(2026, 10, 2, 9, 30, tzinfo=core.ET)
    mins = [{"t": (start + timedelta(minutes=i)).isoformat(), "vw": 500 + i, "v": 1000} for i in range(8)]
    return {"bars": {"SPY": mins, "QQQ": [dict(m, vw=400 + i) for i, m in enumerate(mins)]}}


def test_index_bars_page_is_retried_and_the_regime_is_read():
    clock = _Clock()
    cards = [_card("AAA", "long"), _card("BBB", "short")]
    with patch.object(core, "urlopen", side_effect=[_busy(503), _Response(_index_bars())]) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"), \
         patch.object(core, "_ledger", lambda row: None):
        regime = adaptive.evaluate_market_regime("2026-10-02", "09:38", cards=cards)
    assert opened.call_count == 2 and clock.slept == [0.5]
    assert regime["spy_slope"] is not None and regime["qqq_slope"] is not None
    assert not any("VWAP" in r for r in regime["reasons"])


def test_persistent_index_bars_failure_sits_the_wave_out_and_is_in_the_ledger():
    clock = _Clock()
    rows = []
    cards = [_card("AAA", "long"), _card("BBB", "short")]
    with patch.object(core, "urlopen", side_effect=OSError("down")) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"), \
         patch.object(core, "_ledger", rows.append):
        regime = adaptive.evaluate_market_regime("2026-10-02", "09:38", cards=cards)
    assert opened.call_count == 3
    assert regime["action"] == "SIT_OUT_CASH"
    assert any("VWAP" in r for r in regime["reasons"])
    assert rows == [{"kind": "indices_unavailable", "day": "2026-10-02",
                     "why": "index bars provider request failed",
                     "detail": {"attempts": 3, "cause": "OSError: down"}}]


def test_retry_after_above_ten_seconds_is_capped_not_discarded():
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=[_busy(429, retry_after=15), _Response({"news": []})]) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"):
        assert adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=30) == {}
    assert opened.call_count == 2 and clock.slept == [10.0]


def test_no_time_left_is_reported_as_budget_exhausted_with_zero_requests():
    clock = _Clock()
    req = adaptive.urllib.request.Request("https://relay.test/x")
    with patch.object(core, "urlopen") as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic):
        try:
            adaptive._relay_page(req, deadline=clock.now - 1.0)
        except adaptive.NewsDataError as exc:
            assert str(exc) == "news budget exhausted" and exc.detail["attempts"] == 0
        else:
            raise AssertionError("must fail closed")
    assert opened.call_count == 0


def test_a_replay_miss_is_a_transport_error_the_helper_retries_and_reports():
    """ADT's parity transport raises its own RuntimeError (ReplayMiss) rather than an HTTPError; the
    helper must treat it like any transport failure: bounded retries, then fail closed with the cause."""
    class ReplayMiss(RuntimeError):
        pass
    clock = _Clock()
    with patch.object(core, "urlopen", side_effect=ReplayMiss("uncached request")) as opened, \
         patch.object(adaptive._t, "monotonic", clock.monotonic), \
         patch.object(adaptive, "_news_sleep", clock.sleep), \
         patch.object(adaptive, "_record_health"):
        with pytest.raises(adaptive.NewsDataError) as info:
            adaptive.fetch_earnings_headlines(["ABC"], FREEZE, budget_s=30)
    assert opened.call_count == 3
    assert info.value.detail == {"attempts": 3, "cause": "ReplayMiss: uncached request"}
