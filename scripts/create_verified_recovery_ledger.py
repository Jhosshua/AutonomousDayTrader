#!/usr/bin/env python3
"""Create the one-time September 21 aggregate recovery ledger.

Only facts preserved in MEMORY.md are imported. Individual fills, timestamps,
symbols, win/loss counts, and fees are deliberately not invented.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.account import AccountStatus, PaperTradingAccount
from backend.app.core.bracket import DynamicBracketManager
from backend.app.core.engine import ExecutionEngine
from backend.app.core.flattening import FlatteningPhase, ZeroOvernightFlatteningEngine
from backend.app.core.persistence import TradingStateStore
from backend.app.core.risk import InstitutionalRiskEngine
from backend.app.core.runtime_state import capture_runtime_state
from backend.app.strategies.adaptation import DynamicAdaptationEngine
from backend.app.strategies.mean_reversion import MeanReversionStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", help="Destination SQLite database")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).expanduser().resolve()
    if output.exists() and not args.overwrite:
        raise SystemExit(f"Refusing to overwrite existing ledger: {output}")
    if output.exists():
        output.unlink()

    account = PaperTradingAccount()
    account.cash = 49978.66
    account.realized_pnl = -21.34
    account.daily_starting_equity = 50000.0
    account.status = AccountStatus.EOD_FLAT
    account._recompute_account_state()

    engine = ExecutionEngine(account)
    brackets = DynamicBracketManager()
    risk = InstitutionalRiskEngine()
    risk.evaluate_account_state(
        equity=account.equity,
        cash=account.cash,
        realized_pnl=account.realized_pnl,
        unrealized_pnl=0.0,
        timestamp=datetime.now(timezone.utc),
    )
    flattening = ZeroOvernightFlatteningEngine()
    flattening.current_phase = FlatteningPhase.MARKET_CLOSED
    flattening.phase1_executed = True
    flattening.phase2_executed = True
    flattening.phase3_executed = True
    flattening.phase4_executed = True
    flattening.audit_passed = True
    adaptation = DynamicAdaptationEngine()
    strategies = [
        OpeningRangeBreakoutStrategy(),
        VWAPPullbackStrategy(),
        NewsMomentumStrategy(),
        MeanReversionStrategy(),
    ]
    strategies[0].daily_pnl = -12.09
    strategies[0].trades_count = 2
    strategies[1].daily_pnl = -9.25
    strategies[1].trades_count = 3

    store = TradingStateStore(str(output))
    summary = {
        "session_date": "2026-09-21",
        "opening_equity": 50000.0,
        "closing_equity": 49978.66,
        "realized_pnl": -21.34,
        "trades_count": 5,
        "wins": None,
        "losses": None,
        "fees": 0.0,
        "fees_known": False,
        "strategies": {
            "orb": {"trades_count": 2, "realized_pnl": -12.09},
            "vwap_pullback": {"trades_count": 3, "realized_pnl": -9.25},
            "news_momentum": {"trades_count": 0, "realized_pnl": 0.0},
            "mean_reversion": {"trades_count": 0, "realized_pnl": 0.0},
        },
        "source": "LEGACY_SUMMARY_IMPORT",
        "aggregate_only": True,
        "note": "Verified session aggregate recovered from MEMORY.md; individual executions were not persisted by the prior release.",
    }
    payload = capture_runtime_state(
        account=account,
        engine=engine,
        bracket_manager=brackets,
        risk_engine=risk,
        flattening_engine=flattening,
        adaptation_engine=adaptation,
        strategies=strategies,
        entry_order_to_bracket={},
        bracket_realized_pnl={},
        completed_brackets_recorded=set(),
        latest_market_prices={},
        market_history={},
        recent_news=[],
        last_session_date=date(2026, 9, 21),
        last_vix_print=None,
        ledger_revision=0,
    )
    store.save_checkpoint(payload, "LEGACY_SUMMARY_IMPORT", session_summaries=[summary])
    store.integrity_check()
    store.close()
    print(f"Created verified aggregate recovery ledger at {output}")
    print("Recovered equity: $49,978.66; session trades: 5; session P&L: -$21.34")


if __name__ == "__main__":
    main()
