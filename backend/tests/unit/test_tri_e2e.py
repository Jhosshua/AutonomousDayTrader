"""Full production bar-handler dry runs; broker and network always detached."""
import pytest

from scripts import run_tri_engine_dry_run as dry


@pytest.fixture(autouse=True)
def isolated_runtime():
    statuses = {s.strategy_id: s.status for s in dry.r.strategies}
    relays = dict(dry.r.relay_statuses)
    with dry.no_network():
        yield
    dry.r.reset_runtime_state()
    dry.r.set_simulation_mode(False)
    dry.r.relay_statuses.update(relays)
    for s in dry.r.strategies:
        s.status = statuses[s.strategy_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,side,kind", dry.CASES)
async def test_actual_handler_tranche_rules_and_ledger(symbol, side, kind):
    row, _ = await dry.replay_case(symbol, side, kind, capture=False)
    assert row["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol,side", [("TSLA", "LONG"), ("TSLA", "SHORT"), ("CDE", "LONG"), ("CDE", "SHORT")])
async def test_context_before_stock_produces_the_same_execution(symbol, side):
    row, _ = await dry.replay_case(symbol, side, "target", capture=False, reverse=True)
    assert row["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_simultaneous_symbols_coexist_with_enabled_arms_and_existing_generic_risk(reverse):
    row, _ = await dry.replay_coexistence(reverse=reverse)
    assert row["passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol", ["TSLA", "CDE"])
async def test_no_signal_day_never_routes_an_entry(symbol):
    assert (await dry.replay_no_signal(symbol))["passed"]


@pytest.mark.asyncio
async def test_no_new_cde_execution_at_noon():
    assert (await dry.replay_noon_freeze())["passed"]
