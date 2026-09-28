"""OrbsFacade on the checked-in synthetic session (replay transport, no network) and on small fakes."""
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from backend.app.strategies.orbs import config, flow, shim
from backend.app.strategies.orbs.facade import OrbsFacade
from backend.tests.unit.orbs._helpers import SYNTH_DAY, FakeHTTP, parity_modules, unpack_fixture

ET = shim.ET
D = date.fromisoformat(SYNTH_DAY)


def at(h, m, s=0):
    return datetime(D.year, D.month, D.day, h, m, s, tzinfo=ET)


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    common, _, _ = parity_modules()
    root = tmp_path_factory.mktemp("orbs_fixture")
    unpack_fixture(str(root))
    old = common.CACHE_ROOT
    common.CACHE_ROOT = str(root)
    try:
        yield common
    finally:
        common.CACHE_ROOT = old


@pytest.fixture
def fac(replay, tmp_path):
    transport = replay.Transport(SYNTH_DAY, "replay")
    f = OrbsFacade(state_dir=str(tmp_path / "state"), relay_base=replay.RELAY_ROOT, relay_token="t",
                   http=transport)
    f.transport = transport
    assert f.prep(D)["ok"]
    yield f
    config.set_exclude_symbols(())


def primary(fac):
    board = fac.scan(D, at(9, 38), "primary", set())
    assert board["ok"], board["error"]
    return board


def test_prep_builds_the_universe_without_funds(fac):
    out = fac.prep(D)
    assert out["ok"] and out["universe_size"] == 7
    assert "SPY" not in out["universe"] and set(out["universe"]) >= {"AMD", "XOM", "PLTR", "BA"}
    assert out["closes"] == 7 and out["atr"] == 7


def test_state_files_go_to_the_state_dir_not_the_repo(fac, tmp_path):
    primary(fac)
    assert (tmp_path / "state" / "cards_today.jsonl").exists()
    assert fac.state_dir == str(tmp_path / "state")


def test_primary_scan_board(fac):
    board = primary(fac)
    by = {c["symbol"]: c for c in board["cards"]}
    assert set(by) == {"AMD", "XOM", "CAT", "JPM", "NKE"}
    assert board["coverage"] == 1.0 and len(board["board_id"]) == 16
    assert by["AMD"]["direction"] == "long" and by["XOM"]["direction"] == "short"
    assert by["CAT"]["candle"] == "green" and by["CAT"]["board_direction"] == "short"      # wrong-way break
    assert by["NKE"]["flow_blocked"] is True and by["NKE"]["velocity_pass"] is False       # slow crossing
    assert by["AMD"]["delta_pass"] is True and by["AMD"]["velocity_pass"] is True
    assert by["AMD"]["stop"] < by["AMD"]["entry"] < by["AMD"]["H_ORB"] + 1


def test_scan_argument_guards(fac):
    with pytest.raises(ValueError):
        fac.scan(D, at(9, 38), "primary", {"AMD"})       # primary never skips symbols
    with pytest.raises(ValueError):
        fac.scan(D, at(9, 38, 30), "primary", set())      # whole minutes only
    with pytest.raises(ValueError):
        fac.scan(D, at(9, 38), "bogus", set())


def test_decide_primary_picks_and_audit(fac):
    board = primary(fac)
    out = fac.decide(D, board, "primary", at(9, 39), set())
    assert out["verdict"] == "trade" and out["valid"] is True
    assert [p["symbol"] for p in out["picks"]] == ["XOM", "AMD"]
    xom, amd = out["picks"]
    assert (xom["tier"], xom["side"], xom["direction"]) == ("earnings", "sell", "short")
    assert (amd["tier"], amd["side"]) == ("structure", "buy")
    assert amd["entry"] == amd["card"]["entry"] and amd["stop"] == amd["card"]["stop"]
    why = {a["symbol"]: a["why"] for a in out["audit"]}
    assert why["CAT"].startswith("Candle rule:")
    assert why["NKE"].startswith("Flow rule:")
    assert "Not selected" in why["JPM"]
    assert out["regime"]["classification"] == "CALM_TREND" and out["regime"]["short_frac"] == 0.4


def test_decide_occupied_pick_is_refused_not_replaced(fac):
    out = fac.decide(D, primary(fac), "primary", at(9, 39), {"XOM"})
    assert [p["symbol"] for p in out["picks"]] == ["AMD"]
    assert out["refused"] == [{"symbol": "XOM", "reason": "already held by this account"}]


def test_exclusion_keeps_the_board_and_promotes_the_next_pick(fac):
    board = primary(fac)
    base = fac.decide(D, board, "primary", at(9, 39), set())
    fac.set_exclude_symbols({"XOM"})
    out = fac.decide(D, board, "primary", at(9, 39), set())
    assert out["cards"] == base["cards"]                             # board unchanged
    assert out["regime"] == base["regime"]                           # short fraction unchanged (0.4)
    assert [p["symbol"] for p in out["picks"]] == ["AMD", "JPM"]     # JPM promoted into the second slot
    xom_row = next(a for a in out["audit"] if a["symbol"] == "XOM")
    assert xom_row["survives"] is False and "excluded_symbol" in xom_row["why"]
    fac.set_exclude_symbols({"XOM", "AMD"})
    out2 = fac.decide(D, board, "primary", at(9, 39), set())
    assert [p["symbol"] for p in out2["picks"]] == ["JPM"]           # next eligible becomes pick 1
    assert out2["regime"]["short_frac"] == 0.4


def test_secondary_scan_skips_occupied_symbols(fac):
    primary(fac)
    full = fac.scan(D, at(9, 51), "secondary", set(), executed_today=set())
    assert {c["symbol"] for c in full["cards"]} == {"PLTR", "BA"}
    skipped = fac.scan(D, at(9, 52), "secondary", {"PLTR"}, executed_today=set())
    assert {c["symbol"] for c in skipped["cards"]} == {"BA"}
    assert skipped["health"]["attempted"] == 6


def test_secondary_one_sided_board_sits_out(fac):
    primary(fac)
    board = fac.scan(D, at(9, 48), "secondary", set(), executed_today=set())
    out = fac.decide(D, board, "secondary", at(9, 48, 30), set())
    assert out["verdict"] == "sit_out" and out["picks"] == []
    assert out["regime"]["classification"] == "ONE_SIDED"


def test_recheck_candle_flow_macro(fac):
    by = {c["symbol"]: c for c in primary(fac)["cards"]}
    assert fac.recheck(by["AMD"], at(9, 39)) == (True, "")
    ok, why = fac.recheck(by["CAT"], at(9, 39))
    assert not ok and why.startswith("candle rule:")
    ok, why = fac.recheck(by["NKE"], at(9, 39))
    assert not ok and why.startswith("flow rule:")


def test_macro_veto_fails_closed_without_data(fac):
    assert fac.macro_veto("AMD", "long", at(9, 39)) == (False, "")      # recorded, flat SPY/XLK
    vetoed, why = fac.macro_veto("AMD", "long", at(9, 41, 17))          # nothing recorded for this time
    assert vetoed and "unavailable" in why
    assert fac.transport.misses                                          # the replay recorded the miss


# ---------------- small fakes (no fixture) ----------------
def _bars(move_pct):
    def handler(path, params):
        start = datetime.fromisoformat(params["start"]).astimezone(ET)
        end = datetime.fromisoformat(params["end"]).astimezone(ET)
        rows, t = {}, start
        for etf in params["symbols"].split(","):
            out, t = [], start
            while t + timedelta(minutes=1) <= end:
                c = 100.0 * (1 + move_pct / 100.0) if etf == "SPY" else 100.0
                out.append({"t": t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "o": 100.0, "c": c,
                            "v": 1, "vw": 100.0})
                t += timedelta(minutes=1)
            rows[etf] = out
        return {"bars": rows, "next_page_token": None}
    return handler


def test_macro_veto_blocks_a_long_while_spy_falls(tmp_path):
    fac = OrbsFacade(str(tmp_path), "https://relay.invalid", "t", http=FakeHTTP(_bars(-0.30)))
    vetoed, why = fac.macro_veto("AMD", "long", at(9, 45))
    assert vetoed and why.startswith("SPY is down")
    assert fac.macro_veto("AMD", "short", at(9, 45)) == (False, "")
    fac2 = OrbsFacade(str(tmp_path), "https://relay.invalid", "t", http=FakeHTTP(_bars(+0.02)))  # inside deadband
    assert fac2.macro_veto("AMD", "long", at(9, 45)) == (False, "")


def _latest(age_s):
    def handler(path, params):
        assert path == "/data/v2/stocks/AMD/trades/latest" and params == {"feed": "sip"}
        stamp = (at(9, 40) - timedelta(seconds=age_s)).astimezone(timezone.utc)
        return {"symbol": "AMD", "trade": {"t": stamp.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"), "p": 151.25, "s": 5}}
    return handler


def test_latest_trade_age_rule(tmp_path):
    fac = OrbsFacade(str(tmp_path), "https://relay.invalid/", "t", http=FakeHTTP(_latest(10)))
    with shim.frozen_clock(at(9, 40)):
        got = fac.latest_trade("AMD")
    assert got["price"] == 151.25 and 9.8 < got["age_s"] < 10.0
    fac = OrbsFacade(str(tmp_path), "https://relay.invalid/", "t", http=FakeHTTP(_latest(61)))
    with shim.frozen_clock(at(9, 40)):
        assert fac.latest_trade("AMD") is None          # older than 60 s
    fac = OrbsFacade(str(tmp_path), "https://relay.invalid/", "t", http=FakeHTTP(_latest(-6)))
    with shim.frozen_clock(at(9, 40)):
        assert fac.latest_trade("AMD") is None          # more than 5 s in the future


def _tape_against_long(path, params):
    kind = path.rsplit("/", 1)[1]
    start = datetime.fromisoformat(params["start"])
    if kind == "quotes":
        rows = [{"t": (start + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z"),
                 "bp": 100.0, "ap": 100.02, "bs": 500, "as": 500}]
    else:
        rows = [{"t": (start + timedelta(seconds=2 + i * 0.5)).strftime("%Y-%m-%dT%H:%M:%S.%f000Z"),
                 "p": 100.0, "s": 100, "c": ["@"]} for i in range(25)]         # every print hits the bid
    return {kind: rows, "next_page_token": None}


def test_absorption_poll_never_blocks_and_returns_fresh_reads(tmp_path):
    fac = OrbsFacade(str(tmp_path), "https://relay.invalid", "t", http=FakeHTTP(_tape_against_long))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first = fac.absorption_poll("AMD", "long", now)
    assert first is None                                  # only schedules the background read
    got = None
    for _ in range(100):
        got = fac.absorption_poll("AMD", "long", now + timedelta(seconds=1))
        if got is not None:
            break
        time.sleep(0.02)
    assert got is not None and got["fire"] is True and got["ratio"] == -1.0 and got["trades"] == 25
    # a read older than ABSORPTION_MAX_RESULT_AGE_S is never handed back
    assert not flow.fresh_read(got, now + timedelta(seconds=config.ABSORPTION_MAX_RESULT_AGE_S + 1))
