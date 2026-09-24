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
    "CONCURRENCY": "Maximum open positions reached",
    "SIZING": "Position size worked out to 0 shares",
    "RISK": "Blocked by the risk limits",
    "ENGINE_REJECT": "Order rejected by the execution engine",
    "BAD_PRICE": "Signal had no valid price",
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
