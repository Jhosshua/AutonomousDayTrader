"""Signal decision log: what each strategy wanted to do and what the bot decided.

Primitive data only (lists/dicts of str/int/float) so it can ride in the runtime
checkpoint as an optional key without touching strategy state.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("AutonomousDayTrader.decisions")
ET = ZoneInfo("America/New_York")

MAX_RECORDS = 300

# Fixed outcome codes -> plain sentence for the operator.
OUTCOME_TEXT = {
    "SUBMITTED": "Order sent",
    "ARBITRATION_LOST": "Another strategy had priority on this stock",
    "DUPLICATE": "Already has a trade or order on this stock",
    "PHASE_GATE": "Outside this strategy's trading hours",
    "MARKET_FILTER": "Blocked by the market-direction check",
    "RS_FILTER": "Stock is not leading the market (relative strength)",
    "MACRO_BLACKOUT": "Scheduled macro release too close (macro regime filter)",
    "SECTOR_AGAINST": "Its sector ETF is moving the other way",
    "SECTOR_RS_FILTER": "Stock is not leading its sector",
    "MACRO_WIND_AGAINST": "Dollar or rates are moving against this trade",
    "REGIME_UNAVAILABLE": "Sector, dollar or rates data not available",
    "SESSION_DELTA_AGAINST": "Net buying/selling on the day is against this trade",
    "SPREAD_WIDE": "Bid-ask spread is wider than normal (liquidity thin)",
    "HVN_NO_SUPPORT": "The dip did not land on a prior high-volume level",
    "HVN_OVERHEAD": "A prior high-volume level sits right in the way",
    "PROFILE_UNAVAILABLE": "Prior-days volume profile not available",
    "CONCURRENCY": "Maximum open positions reached",
    "SIZING": "Position size worked out to 0 shares",
    "RISK": "Blocked by the risk limits",
    "ENGINE_REJECT": "Order rejected by the execution engine",
    "BAD_PRICE": "Signal had no valid price",
    # Opening Range Breakout (ORBStraddle rules): its own scheduler and controller decide and trade
    "ORB_SAT_OUT": "ORB sat out this decision",
    "ORB_NO_DECISION": "ORB made no decision (the scan or board could not be used)",
    "ORB_SHADOW": "Shadow mode: would have placed this order",
    "ORB_REFUSED": "Picked, but not placed (a pre-order check refused it)",
    "ORB_NOT_EXECUTED": "Picked, but not placed (ORB could not trade then)",
    "ORB_UNKNOWN": "Order sent; the broker reply was lost and is being checked",
}


def classify_adaptation_reason(reason: str) -> str:
    if reason.startswith("PHASE_GATE"):
        return "PHASE_GATE"
    if reason.startswith("ADAPTATION_MARKET_FILTER") or "INDEX_" in reason:
        return "MARKET_FILTER"
    if reason.startswith("CONCURRENCY"):
        return "CONCURRENCY"
    if reason.startswith("SIZING"):
        return "SIZING"
    return "RISK"


class DecisionLog:
    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self.counts: Dict[str, Dict[str, int]] = {}
        self.session_date: Optional[str] = None
        self._seq = 0

    def record(
        self,
        strategy_id: str,
        symbol: str,
        side: str,
        price: float,
        outcome: str,
        detail: str,
        when: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        when = when or datetime.now(ET)
        if self.session_date is None:
            self.session_date = when.astimezone(ET).date().isoformat()
        self._seq += 1
        side_txt = str(side).split(".")[-1].upper()
        rec = {
            "id": self._seq,
            "time": when.astimezone(ET).isoformat(timespec="seconds"),
            "strategy_id": strategy_id,
            "symbol": symbol.upper(),
            "side": side_txt,
            "price": round(float(price or 0.0), 4),
            "outcome": outcome,
            "outcome_text": OUTCOME_TEXT.get(outcome, outcome),
            "detail": str(detail)[:200],
        }
        self.records.append(rec)
        del self.records[:-MAX_RECORDS]
        per = self.counts.setdefault(strategy_id, {})
        per[outcome] = per.get(outcome, 0) + 1
        log.info(
            "DECISION %s %s %s @%.2f -> %s (%s)",
            strategy_id, rec["symbol"], side_txt, rec["price"], outcome, rec["detail"],
        )
        return rec

    def summary(self, strategy_id: str) -> Dict[str, Any]:
        per = self.counts.get(strategy_id, {})
        signals = sum(per.values())
        submitted = per.get("SUBMITTED", 0)
        blocked = {k: v for k, v in per.items() if k != "SUBMITTED"}
        top = max(blocked.items(), key=lambda kv: kv[1])[0] if blocked else None
        return {
            "signals_today": signals,
            "orders_today": submitted,
            "blocked_today": signals - submitted,
            "top_block_reason": top,
            "top_block_text": OUTCOME_TEXT.get(top) if top else None,
            "blocked_by_reason": blocked,
        }

    def recent(self, limit: int = 50, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = [r for r in self.records if not strategy_id or r["strategy_id"] == strategy_id]
        return list(reversed(rows[-limit:]))

    def reset_for_session(self, session_date: Optional[str]) -> Dict[str, Dict[str, int]]:
        """Start a new session; returns the finished session's counts for the summary."""
        finished = {k: dict(v) for k, v in self.counts.items()}
        self.counts.clear()
        self.records.clear()
        self.session_date = session_date
        return finished

    def to_state(self) -> Dict[str, Any]:
        return {
            "records": [dict(r) for r in self.records],
            "counts": {k: dict(v) for k, v in self.counts.items()},
            "session_date": self.session_date,
            "seq": self._seq,
        }

    def load_state(self, state: Optional[Dict[str, Any]]) -> None:
        if not isinstance(state, dict):
            return
        self.records = [dict(r) for r in state.get("records", [])][-MAX_RECORDS:]
        self.counts = {k: {kk: int(vv) for kk, vv in v.items()} for k, v in state.get("counts", {}).items()}
        self.session_date = state.get("session_date")
        self._seq = int(state.get("seq", len(self.records)))


decision_log = DecisionLog()
