"""ADT's "orb" strategy: Opening Range Breakout by ORBStraddle's rules (since 2026-09-28).

The decisions are made by ORBStraddle's copied decision code (backend/app/strategies/orbs/, behind
OrbsFacade) on a 250-stock SIP board, and the orders are Alpaca brackets placed and supervised by
the ORB controller (backend/app/core/orb_execution.py) on its scheduler (orb_scheduler.py). This
class only carries the strategy id "orb" (checkpoint strategy-set rule), the operator status and
today's P&L counters for the dashboard. It never emits a signal: ADT's generic bar loop,
arbitration, market filter, phase gate, VIX sizing and allocation caps never see ORB.

The old bar-based ORB (5-minute range, RVOL vs 20 bars, one breakout per symbol until 11:30) was
removed in the same change; git history keeps it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from backend.app.models.events import BarEvent
from backend.app.strategies.base import SignalEvent, Strategy

STRATEGY_ID = "orb"
DISPLAY_NAME = "Opening Range Breakout (ORBStraddle rules)"
HOURS_TEXT = "Decides 9:38 AM, may add trades until 10:15 AM, closes by 11:00 AM"


@dataclass
class SymbolORBState:
    """Compatibility only: the old ORB's per-symbol state class, kept importable so a checkpoint
    written before 2026-09-28 still decodes. Its contents are dropped at restore (checkpoint
    schema 2 -> 3), never merged into the new ORB. Do not use it for anything else."""
    opening_bars: List[BarEvent] = field(default_factory=list)
    all_bars: List[BarEvent] = field(default_factory=list)
    range_established: bool = False
    range_high: float = 0.0
    range_low: float = 0.0
    range_midpoint: float = 0.0
    baseline_volume: float = 100000.0
    breakout_fired: bool = False


class OrbStrategy(Strategy):
    """Dashboard/status carrier for the ORB controller. Never emits signals."""

    def __init__(self, strategy_id: str = STRATEGY_ID, name: str = DISPLAY_NAME):
        super().__init__(strategy_id=strategy_id, name=name)

    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        return []
