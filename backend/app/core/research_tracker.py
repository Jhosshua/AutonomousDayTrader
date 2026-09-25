"""Builds research rows from signals, fills and bars (observation only).

The app calls these hooks from the trading path, always through
``research.safe`` so a failure here is logged and counted and can never change
what the bot does. Nothing here mutates trading objects: brackets, orders,
positions and strategies are only read.

Rows produced (see PLAN_2026_09_25_backtest_tracking.md):
* ``signals``: every intraday signal decision with the strategy's features, the
  admission stages (adapted stop, sizes) and a settings/market snapshot.
* ``trades``: every closed intraday bracket (incl. TSLA OR15) and every closed
  swing round trip, with the stop ladder, quantity ladder, R, MFE/MAE with a
  coverage verdict, per-fill timing/slippage/intent and provenance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.core.research import (
    MAX_STOP_HISTORY,
    ResearchRecorder,
    excursion_summary,
    fold_bar,
    fold_price,
    iso,
    json_safe,
    minute_floor,
    new_excursion,
    parse_ts,
    rnd,
)

ET = ZoneInfo("America/New_York")
MAX_FILLS_PER_TRADE = 100
STALE_UNFILLED_HOURS = 24
MAX_STATE_BYTES = 2_000_000


def _enum(value: Any) -> Any:
    return getattr(value, "value", value)


class ResearchTracker:
    def __init__(
        self,
        recorder: ResearchRecorder,
        *,
        bracket_manager: Any,
        entry_order_to_bracket: Dict[str, str],
        account: Any,
        adaptation_engine: Any,
        market_filter: Callable[[], Any],
        risk_engine: Any,
        strategy_map: Dict[str, Any],
        swing_staged_order_manager: Any,
        execution_mode: Callable[[], str],
        committed_count: Callable[[], int],
        vix_print: Callable[[], Any] = lambda: None,
        order_lookup: Callable[[str], Any] = lambda _oid: None,
        run_id: str = "",
        swing_strategy_id: str = "swing_panic_dip",
        or15_strategy_id: str = "tsla_or15_retest",
    ) -> None:
        self.recorder = recorder
        self.bracket_manager = bracket_manager
        self.entry_order_to_bracket = entry_order_to_bracket
        self.account = account
        self.adaptation_engine = adaptation_engine
        self.market_filter = market_filter
        self.risk_engine = risk_engine
        self.strategy_map = strategy_map
        self.swing_staged = swing_staged_order_manager
        self.execution_mode = execution_mode
        self.committed_count = committed_count
        self.swing_id = swing_strategy_id
        self.or15_id = or15_strategy_id
        self.vix_print = vix_print
        self.order_lookup = order_lookup
        self.run_id = run_id
        # Latest SUBMITTED signal row per (strategy, symbol): links OR15 trades,
        # whose brackets are created by their own controller.
        self.last_submitted: Dict[str, Dict[str, Any]] = {}
        # Open-trade research state. Small, JSON-safe, checkpointed.
        self.state: Dict[str, Dict[str, Any]] = {"brackets": {}, "swing": {}}

    # ------------------------------------------------------------ checkpoint
    def to_state(self) -> Optional[str]:
        """The checkpoint carries research state as ONE JSON string, so nothing in it
        can be type-decoded (or break) during the trading restore. None if oversized."""
        import json
        raw = json.dumps(json_safe(self.state), allow_nan=False, separators=(",", ":"))
        if len(raw) > MAX_STATE_BYTES:
            self.recorder._fail(RuntimeError(f"research state {len(raw)} bytes; omitted from checkpoint"))
            return None
        return raw

    def load_state(self, state: Any) -> None:
        import json
        if isinstance(state, str):
            state = json.loads(state)
        if isinstance(state, dict):
            self.state = {
                "brackets": dict(state.get("brackets") or {}),
                "swing": dict(state.get("swing") or {}),
            }

    # --------------------------------------------------------------- context
    @staticmethod
    def code_revision() -> str:
        return (os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT_SHA") or "unknown")[:40]

    def mode_tag(self) -> str:
        """Row-id namespace. Replays get their own run id so repeated replays never collide."""
        mode = self.execution_mode()
        return f"{mode}[{self.run_id}]" if mode == "replay" and self.run_id else mode

    def context(self, strategy_id: Optional[str], symbol: Optional[str], asof: Optional[datetime],
                signal: Any = None) -> Dict[str, Any]:
        """Settings + market snapshot. Each part fails independently."""
        ctx: Dict[str, Any] = {
            "execution_mode": self.execution_mode(),
            "code_revision": self.code_revision(),
            "captured_at": iso(datetime.now(timezone.utc)),
        }

        def part(name: str, build: Callable[[], Any]) -> None:
            try:
                ctx[name] = build()
            except Exception as exc:
                ctx[name] = {"error": f"{type(exc).__name__}: {exc}"}

        strat = self.strategy_map.get(strategy_id or "")
        if strategy_id == self.or15_id:
            ctx["strategy_params"] = {"note": "fixed OR15 protocol; see tsla_or15_implementation_sha256 in the session summary"}
        elif strat is not None and hasattr(strat, "tuning_params"):
            part("strategy_params", strat.tuning_params)
        ae = self.adaptation_engine
        part("adaptation", lambda: {
            "vix": rnd(getattr(ae, "current_vix", None), 3),
            "vix_regime": _enum(getattr(ae, "current_vix_regime", None)),
            "sizing_multiplier": rnd(getattr(ae, "current_sizing_multiplier", None), 3),
            "stop_multiplier": rnd(getattr(ae, "current_stop_multiplier", None), 3),
            "time_phase": _enum(getattr(ae, "current_time_phase", None)),
            "max_concurrent_positions": getattr(ae, "max_concurrent_positions", None),
            "base_risk_pct": getattr(ae, "base_risk_pct", None),
            "max_alloc_pct": getattr(ae, "max_alloc_pct", None),
        })
        part("vix_print", lambda: self._vix_print())
        # Admission judges the index filter at the signal's bar time; so does this.
        part("market_filter", lambda: self._market_snapshot(asof or datetime.now(timezone.utc)))
        if signal is not None:
            part("market_filter_verdict", lambda: self._filter_verdict(signal))
        part("risk", lambda: {
            "equity": rnd(self.account.equity, 2),
            "day_start_equity": rnd(self.account.daily_starting_equity, 2),
            "day_pnl": rnd(self.account.equity - self.account.daily_starting_equity, 2),
            "buying_power": rnd(getattr(self.account, "buying_power", None), 2),
            "open_positions": len(self.account.positions),
            "committed_intraday_positions": self.committed_count(),
            "breaker": _enum(getattr(self.risk_engine, "status", None)),
        })
        if asof is not None:
            part("time", lambda: self._time_parts(asof))
        return ctx

    def _market_snapshot(self, asof: datetime) -> Dict[str, Any]:
        snap = self.market_filter().get_trend_snapshot(asof)
        data = snap.model_dump() if hasattr(snap, "model_dump") else vars(snap)
        return json_safe(data)

    def _filter_verdict(self, signal: Any) -> Dict[str, Any]:
        """The same (read-only) question admission asks, with the same inputs."""
        permitted, reason = self.market_filter().is_signal_permitted(
            strategy_id=signal.strategy_id, side=signal.side, symbol=signal.symbol,
            asof=signal.timestamp, catalyst_sentiment=getattr(signal, "catalyst_sentiment", None),
            volume_surge=getattr(signal, "volume_surge", None), rvol=getattr(signal, "rvol", None),
        )
        return {"permitted": bool(permitted), "reason": str(reason)[:300],
                "applied_in_admission": getattr(self.adaptation_engine, "market_filter", None) is not None}

    def _vix_print(self) -> Optional[Dict[str, Any]]:
        vp = self.vix_print()
        if vp is None:
            return None
        return {
            "value": rnd(getattr(vp, "value", None), 3),
            "received_at": iso(getattr(vp, "received_at", None)),
            "is_stale": bool(getattr(vp, "is_stale", False)),
            "is_fallback": bool(getattr(vp, "is_fallback", False)),
        }

    @staticmethod
    def _time_parts(asof: datetime) -> Dict[str, Any]:
        et = (asof if asof.tzinfo else asof.replace(tzinfo=timezone.utc)).astimezone(ET)
        open_et = et.replace(hour=9, minute=30, second=0, microsecond=0)
        return {
            "et": et.isoformat(),
            "minutes_since_open": int((et - open_et).total_seconds() // 60),
            "weekday": et.strftime("%a"),
        }

    # --------------------------------------------------------------- signals
    def signal_id(self, signal: Any) -> str:
        side = str(_enum(signal.side)).upper()
        return f"{self.mode_tag()}:{signal.strategy_id}|{signal.symbol.upper()}|{side}|{iso(signal.timestamp)}"

    @staticmethod
    def signal_dict(signal: Any) -> Dict[str, Any]:
        return {
            "timestamp": iso(signal.timestamp),
            "bar_end": iso(signal.timestamp + timedelta(minutes=1)) if isinstance(signal.timestamp, datetime) else None,
            "side": str(_enum(signal.side)).upper(),
            "order_type": str(_enum(signal.order_type)).upper(),
            "entry_price": rnd(signal.entry_price),
            "floored_stop": rnd(signal.stop_loss),
            "target_1": rnd(signal.take_profit_1),
            "target_2": rnd(signal.take_profit_2),
            "target_1_is_r_fallback": bool(getattr(signal, "target_1_is_r_fallback", False)),
            "target_2_is_r_fallback": bool(getattr(signal, "target_2_is_r_fallback", False)),
            "confidence": rnd(signal.confidence, 3),
            "reason": str(signal.reason)[:300],
            "rvol": rnd(getattr(signal, "rvol", None)),
            "volume_surge": rnd(getattr(signal, "volume_surge", None)),
            "catalyst_sentiment": rnd(getattr(signal, "catalyst_sentiment", None)),
            "features": json_safe(getattr(signal, "features", None) or {}),
        }

    def record_signal(self, signal: Any, outcome: str, detail: str, stages: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ts = signal.timestamp if isinstance(signal.timestamp, datetime) else None
        session = (ts.astimezone(ET).date().isoformat() if ts else datetime.now(ET).date().isoformat())
        row_id = self.signal_id(signal)
        ctx = self.context(signal.strategy_id, signal.symbol, ts, signal=signal)
        row = {
            "row_id": row_id,
            "kind": "SIGNAL",
            "session_date": session,
            "strategy_id": signal.strategy_id,
            "symbol": signal.symbol.upper(),
            "outcome": outcome,
            "detail": str(detail)[:500],
            "signal": self.signal_dict(signal),
            "stages": json_safe(stages or {}),
            "context": ctx,
            "execution_mode": ctx["execution_mode"],
            "scope_note": (
                "Only signals the strategy emitted. Setups the strategy rejected internally "
                "(e.g. ORB volume/CLV, Mean Reversion z/RSI) never reach this table."
            ),
        }
        self.recorder.record("signals", row_id, row)
        stored = self.recorder.recent["signals"][-1]  # trimmed copy if the row was oversized
        if outcome == "SUBMITTED":
            self.last_submitted[f"{signal.strategy_id}|{signal.symbol.upper()}"] = stored
        return stored

    def open_bracket(self, bracket: Any, signal_row: Optional[Dict[str, Any]], order: Any) -> None:
        """Remember the research view of a new bracket (signal, stages, context)."""
        self.state["brackets"][bracket.bracket_id] = {
            "strategy_id": bracket.strategy_id,
            "signal_id": (signal_row or {}).get("row_id"),
            "signal": (signal_row or {}).get("signal"),
            "stages": (signal_row or {}).get("stages"),
            "context": (signal_row or {}).get("context"),
            "created_at": iso(getattr(bracket, "created_at", None)),
            "created_entry_price": rnd(bracket.entry_price),
            "created_stop": rnd(bracket.initial_stop_price),
            "requested_qty": int(bracket.total_qty),
            "submitted_qty": int(getattr(order, "qty", 0) or 0),
            "submitted_at": iso(getattr(order, "submitted_at", None)),
            "entry_order_id": getattr(order, "id", None),
            "excursion": new_excursion(),
            "stop_history": [],
            "fills": [],
            "started_with_trade": True,
        }

    def _entry_for(self, bracket_id: str, started: bool) -> Dict[str, Any]:
        rs = self.state["brackets"].get(bracket_id)
        if rs is None:
            bracket = self.bracket_manager.brackets.get(bracket_id)
            rs = {
                "strategy_id": getattr(bracket, "strategy_id", None),
                "signal_id": None, "signal": None, "stages": None, "context": None,
                "created_at": iso(getattr(bracket, "created_at", None)),
                "created_entry_price": rnd(getattr(bracket, "entry_price", None)),
                "created_stop": rnd(getattr(bracket, "initial_stop_price", None)),
                "requested_qty": int(getattr(bracket, "total_qty", 0) or 0),
                "submitted_qty": None, "submitted_at": None, "entry_order_id": None,
                "excursion": new_excursion(), "stop_history": [], "fills": [],
                "started_with_trade": started,
                "late_start_reason": None if started else "no research state for this bracket (opened before recording, or state omitted)",
            }
            if bracket is not None and getattr(bracket, "strategy_id", None) == self.or15_id:
                linked = self.last_submitted.get(f"{self.or15_id}|{bracket.symbol}")
                created = getattr(bracket, "created_at", None)
                same_day = bool(linked and created is not None
                                and linked.get("session_date") == (created if created.tzinfo else created.replace(tzinfo=timezone.utc)).astimezone(ET).date().isoformat())
                if linked and started and same_day:
                    rs.update(signal_id=linked.get("row_id"), signal=linked.get("signal"),
                              stages=linked.get("stages"), context=linked.get("context"))
                else:
                    rs["context"] = self.context(self.or15_id, getattr(bracket, "symbol", None), None)
            self.state["brackets"][bracket_id] = rs
        return rs

    def on_activation(self, bracket_id: str) -> None:
        bracket = self.bracket_manager.brackets.get(bracket_id)
        if bracket is None:
            return
        rs = self._entry_for(bracket_id, started=True)
        if rs.get("activation"):
            return
        rs["activation"] = {
            "protected_qty": int(bracket.total_qty),
            "target_1_qty": int(bracket.target_1_qty),
            "target_2_qty": int(bracket.target_2_qty),
            "target_1_price": rnd(bracket.target_1_price),
            "target_2_price": rnd(bracket.target_2_price),
            "target_1_r_config": bracket.target_1_r,
            "target_2_r_config": bracket.target_2_r,
            "target_1_override": rnd(bracket.target_1_override),
            "target_2_override": rnd(bracket.target_2_override),
            "first_fill_price": rnd(bracket.entry_price),
            "r_distance_first_fill": rnd(bracket.r_distance),
            "stop": rnd(bracket.current_stop_price),
            "trailing_enabled": bool(bracket.use_trailing_target_2),
            "trail_atr_multiplier": rnd(bracket.trail_atr_multiplier, 3),
            "fixed_single_target": bool(getattr(bracket, "fixed_single_target", False)),
        }
        self._note_stop(rs, bracket, getattr(bracket, "updated_at", None), "activation")

    # ------------------------------------------------------------------ fills
    def _fill_meta(self, order: Any, fill: Any, broker_attached: bool) -> Dict[str, Any]:
        side = str(_enum(fill.side)).upper()
        slippage = float(getattr(fill, "slippage", 0.0) or 0.0)
        trigger = fill.price - slippage if side == "BUY" else fill.price + slippage
        broker_ts = getattr(order, "broker_fill_timestamp", None) if broker_attached else None
        if not broker_attached:
            ts_source = "simulator"
        elif broker_ts is not None and fill.timestamp == broker_ts:
            ts_source = "alpaca_filled_at"
        else:
            ts_source = "local_trigger_time"
        return {
            "fill_id": fill.fill_id,
            "order_id": fill.order_id,
            "side": side,
            "qty": int(fill.qty),
            "price": rnd(fill.price),
            "fee": rnd(fill.fee),
            "slippage": rnd(slippage),
            "trigger_price": rnd(trigger),
            "realized_pnl": rnd(fill.realized_pnl, 2),
            "fill_ts": iso(fill.timestamp),
            "fill_ts_source": ts_source,
            "broker_filled_at": iso(broker_ts),
            "booked_at": iso(datetime.now(timezone.utc)),
            "order_type": str(_enum(order.order_type)),
            "order_intent": getattr(order, "strategy_id", None),
            "bracket_role": _enum(getattr(order, "bracket_role", None)),
        }

    def on_fill(self, order: Any, fill: Any, broker_attached: bool) -> None:
        meta = self._fill_meta(order, fill, broker_attached)
        if _enum(getattr(order, "arm", None)) == "SWING" and order.strategy_id == self.swing_id:
            self._swing_fill(order, fill, meta)
            return
        bracket_id = self.entry_order_to_bracket.get(order.id)
        role = "ENTRY" if bracket_id else None
        if bracket_id is None:
            mapping = self.bracket_manager.order_to_bracket.get(order.id)
            if mapping:
                bracket_id, child_type = mapping
                role = str(_enum(child_type))
            elif getattr(order, "parent_order_id", None) in self.bracket_manager.brackets:
                bracket_id = order.parent_order_id
                role = f"FLATTEN:{order.strategy_id}"
        if bracket_id is None:
            return
        bracket = self.bracket_manager.brackets.get(bracket_id)
        if (bracket_id not in self.state["brackets"] and bracket is not None
                and str(_enum(bracket.status)).startswith("COMPLETED")
                and role is not None and not role.startswith("FLATTEN")):
            return  # late fill on a trade whose research row was already written
        rs = self._entry_for(bracket_id, started=(role == "ENTRY"))
        meta["role"] = role
        if bracket is not None:
            meta["bracket_status_before"] = str(_enum(bracket.status))
            meta["stop_in_force"] = rnd(bracket.current_stop_price)
            hist = rs.get("stop_history") or []
            meta["stop_set_by"] = hist[-1][3] if hist and hist[-1][1] == meta["stop_in_force"] else "unrecorded"
            meta["remaining_qty_before"] = int(bracket.remaining_qty)
        if len(rs["fills"]) < MAX_FILLS_PER_TRADE:
            rs["fills"].append(meta)
        fold_price(rs["excursion"], fill.price, fill.timestamp)

    # ------------------------------------------------------------------- bars
    def on_bar(self, bar: Any) -> None:
        """Fold this bar into every open trade on its symbol. Call BEFORE fills are processed."""
        sym = bar.symbol.upper()
        for bracket_id, rs in list(self.state["brackets"].items()):
            try:
                bracket = self.bracket_manager.brackets.get(bracket_id)
                if bracket is None or bracket.symbol != sym:
                    continue
                entry_times = [parse_ts(f.get("fill_ts")) for f in rs["fills"] if f.get("role") == "ENTRY"]
                entry_times = [t for t in entry_times if t is not None]
                if not entry_times:
                    continue
                if str(_enum(bracket.status)) not in ("ACTIVE", "TARGET_1_HIT"):
                    continue
                if bar.timestamp >= minute_floor(min(entry_times)) + timedelta(minutes=1):
                    fold_bar(rs["excursion"], bar.timestamp, bar.high, bar.low)
                self._note_stop(rs, bracket, bar.timestamp, "observed_on_bar")
            except Exception as exc:  # one bad entry must not stop the others
                self.recorder._fail(exc)
        acc = self.state["swing"].get(sym)
        if acc and acc.get("entry_fills"):
            entry_times = [t for t in (parse_ts(f.get("fill_ts")) for f in acc["entry_fills"]) if t is not None]
            if entry_times and bar.timestamp >= minute_floor(min(entry_times)) + timedelta(minutes=1):
                fold_bar(acc["excursion"], bar.timestamp, bar.high, bar.low)
            if acc.get("stop") is None:
                pos = self.account.positions.get(sym)
                stop = getattr(pos, "stop_loss_price", None) if pos is not None else None
                if stop:
                    acc["stop"] = rnd(stop)
                    acc["stop_source"] = "position stop"

    def note_stop_change(self, bracket_id: str, ts: Any, why: str) -> None:
        """Called where the bot moves a stop (breakeven ratchet, trailing, operator)."""
        rs = self.state["brackets"].get(bracket_id)
        bracket = self.bracket_manager.brackets.get(bracket_id)
        if rs is not None and bracket is not None:
            self._note_stop(rs, bracket, ts, why)

    def _note_stop(self, rs: Dict[str, Any], bracket: Any, ts: Any, why: str) -> None:
        hist = rs["stop_history"]
        stop = rnd(bracket.current_stop_price)
        if hist and hist[-1][1] == stop:
            return
        if len(hist) >= MAX_STOP_HISTORY:
            hist.pop(1)  # keep the first entry (initial stop)
        hist.append([iso(ts), stop, str(_enum(bracket.status)), why])

    def _entry_not_filled(self, bracket_id: str, rs: Dict[str, Any], why: str) -> None:
        """A SUBMITTED signal whose entry never filled gets a final-outcome row."""
        if not rs.get("signal_id"):
            return
        order = self.order_lookup(rs.get("entry_order_id") or "")
        row_id = f"{rs['signal_id']}|final"
        self.recorder.record("signals", row_id, {
            "row_id": row_id,
            "kind": "SIGNAL_FINAL",
            "session_date": str((rs.get("signal") or {}).get("timestamp") or "")[:10] or datetime.now(ET).date().isoformat(),
            "strategy_id": rs.get("strategy_id") or "unknown",
            "signal_id": rs["signal_id"],
            "outcome": "ENTRY_NOT_FILLED",
            "detail": why,
            "order_status": str(_enum(getattr(order, "status", None))) if order is not None else None,
            "order_reject_reason": str(getattr(order, "reject_reason", None))[:300] if order is not None else None,
            "bracket_id": bracket_id,
            "execution_mode": self.execution_mode(),
        })

    def prune(self, now: datetime) -> None:
        """Drop state for brackets that never filled (cancelled/rejected entries) or were orphaned."""
        for bracket_id, rs in list(self.state["brackets"].items()):
            try:
                self._prune_one(bracket_id, rs, now)
            except Exception as exc:  # one bad entry must not stop the others
                self.recorder._fail(exc)
                self.state["brackets"].pop(bracket_id, None)

    def _prune_one(self, bracket_id: str, rs: Dict[str, Any], now: datetime) -> None:
        bracket = self.bracket_manager.brackets.get(bracket_id)
        has_fills = bool(rs.get("fills"))
        created = parse_ts(rs.get("created_at"))
        if bracket is None and not has_fills:
            self._entry_not_filled(bracket_id, rs, "bracket removed before any fill")
            self.state["brackets"].pop(bracket_id, None)
            return
        if bracket is not None and str(_enum(bracket.status)) == "CANCELLED" and not has_fills:
            self._entry_not_filled(bracket_id, rs, "entry cancelled or refused before any fill")
            self.state["brackets"].pop(bracket_id, None)
            return
        if not has_fills and created is not None and now - created > timedelta(hours=STALE_UNFILLED_HOURS):
            self._entry_not_filled(bracket_id, rs, "no fill within 24 hours")
            self.state["brackets"].pop(bracket_id, None)
            return
        if bracket is None and has_fills:
            # Dropped by session rollover without a completion row: keep 1 hour, then release.
            last = max((t for t in (parse_ts(f.get("fill_ts")) for f in rs["fills"]) if t), default=created)
            if last is None or now - last > timedelta(hours=1):
                self.state["brackets"].pop(bracket_id, None)
            return
        # A finished bracket normally leaves via complete_bracket; anything
        # left behind (e.g. a fill that arrived after completion) is dropped.
        if bracket is not None and str(_enum(bracket.status)) in ("COMPLETED_PROFIT", "COMPLETED_STOP", "COMPLETED_FLATTEN", "CANCELLED"):
            last = max((parse_ts(f.get("fill_ts")) for f in rs.get("fills", []) if f.get("fill_ts")), default=created)
            if last is not None and now - last > timedelta(hours=1):
                self.state["brackets"].pop(bracket_id, None)

    # ------------------------------------------------------- trade completion
    def complete_bracket(self, bracket_id: str, trade: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        bracket = self.bracket_manager.brackets.get(bracket_id)
        rs = self.state["brackets"].pop(bracket_id, None)
        if rs is None:
            rs = {
                "excursion": new_excursion(), "stop_history": [], "fills": [], "started_with_trade": False,
                "late_start_reason": "no research state for this bracket (opened before recording, or state omitted)",
            }
        mode = self.execution_mode()
        row_id = f"{self.mode_tag()}:{bracket_id}"
        base = {
            "row_id": row_id,
            "kind": "OR15" if bracket is not None and bracket.strategy_id == self.or15_id else "INTRADAY",
            "trade_id": bracket_id,
            "execution_mode": mode,
            "code_revision": self.code_revision(),
            "fees_known": mode != "alpaca_paper",
            "fees_note": "Alpaca paper fills are booked with fee 0; real costs unknown." if mode == "alpaca_paper" else "simulator fee model",
        }
        if trade is None or bracket is None:
            row = {**base, "complete": False, "incomplete_reason": "no entry or exit fills on the ledger row",
                   "session_date": datetime.now(ET).date().isoformat(),
                   "strategy_id": getattr(bracket, "strategy_id", "unknown"), "symbol": getattr(bracket, "symbol", None),
                   "research_state": rs}
            self.recorder.record("trades", row_id, row)
            return row

        legs = trade.get("fill_legs") or []
        entry_order_id = bracket_id[4:] if bracket_id.startswith("brk_") else rs.get("entry_order_id")
        entry_legs = [l for l in legs if l.get("order_id") == entry_order_id]
        exit_legs = [l for l in legs if l.get("order_id") != entry_order_id]
        entry_qty = sum(int(l["qty"]) for l in entry_legs)
        exit_qty = sum(int(l["qty"]) for l in exit_legs)
        avg_entry = sum(l["qty"] * l["price"] for l in entry_legs) / entry_qty if entry_qty else None
        avg_exit = sum(l["qty"] * l["price"] for l in exit_legs) / exit_qty if exit_qty else None
        long = bracket.side == "LONG"
        direction = 1.0 if long else -1.0
        stop_at_entry = rs.get("created_stop") if rs.get("created_stop") is not None else bracket.initial_stop_price
        rps = None
        stop_beyond_fill = False
        if avg_entry is not None and stop_at_entry is not None:
            signed = direction * (avg_entry - stop_at_entry)
            if signed > 0:
                rps = signed
            else:
                stop_beyond_fill = True  # filled at/through the stop: R is undefined
        initial_risk = rps * entry_qty if rps and entry_qty else None
        pnl = float(trade.get("realized_pnl") or 0.0)

        exc = dict(rs.get("excursion") or new_excursion())
        for leg in legs:
            fold_price(exc, float(leg["price"]), leg.get("timestamp"))
        entry_at = min((parse_ts(l["timestamp"]) for l in entry_legs), default=None)
        exit_at = max((parse_ts(l["timestamp"]) for l in exit_legs), default=None)
        excursion = excursion_summary(exc, bracket.side, avg_entry or 0.0, rps, entry_at, exit_at,
                                      bool(rs.get("started_with_trade")))

        stages = rs.get("stages") or {}
        signal = rs.get("signal") or {}
        features = signal.get("features") or {}
        activation = rs.get("activation") or {}
        entry_fill_meta = [f for f in rs.get("fills", []) if f.get("role") == "ENTRY"]
        exit_fill_meta = [f for f in rs.get("fills", []) if f.get("role") != "ENTRY"]
        initial_stop = rnd(stop_at_entry)
        stop_moves = max(0, len(rs.get("stop_history") or []) - 1)

        def stop_regime(meta: Dict[str, Any]) -> Optional[str]:
            if not str(meta.get("role", "")).startswith("STOP"):
                return None
            s = meta.get("stop_in_force")
            if s is None or initial_stop is None:
                return None
            if abs(float(s) - float(initial_stop)) < 1e-6:
                return "initial_stop"
            by = str(meta.get("stop_set_by") or "")
            if by.startswith("breakeven"):
                return "breakeven_stop"
            if by.startswith("trail"):
                return "trailing_stop"
            if by.startswith("operator"):
                return "operator_stop"
            return "moved_stop_cause_unrecorded"

        exits = []
        for meta in exit_fill_meta:
            exits.append({**meta, "stop_regime": stop_regime(meta)})

        effective_t1 = activation.get("target_1_price", rnd(bracket.target_1_price))
        effective_t2 = activation.get("target_2_price", rnd(bracket.target_2_price))

        def r_of(price: Any) -> Optional[float]:
            if price is None or avg_entry is None or not rps:
                return None
            return rnd(direction * (float(price) - avg_entry) / rps, 3)

        signal_entry = signal.get("entry_price")
        entry_slip = (direction * (avg_entry - signal_entry)) if (avg_entry is not None and signal_entry) else None
        row = {
            **base,
            "complete": True,
            "session_date": trade.get("session_date"),
            "strategy_id": bracket.strategy_id,
            "symbol": bracket.symbol,
            "side": bracket.side,
            "exit_reason": trade.get("exit_reason"),
            "signal_id": rs.get("signal_id"),
            "signal_context_available": bool(rs.get("signal")),
            "opened_at": trade.get("opened_at"),
            "closed_at": trade.get("closed_at"),
            "hold_minutes": rnd((exit_at - entry_at).total_seconds() / 60.0, 2) if entry_at and exit_at else None,
            "timestamps": {
                "signal_bar_start": signal.get("timestamp"),
                "signal_bar_end": signal.get("bar_end"),
                "decision_wall_at": stages.get("decision_wall_at"),
                "submitted_at": rs.get("submitted_at"),
                "entry_fills": [{k: f.get(k) for k in ("fill_ts", "fill_ts_source", "broker_filled_at", "booked_at")}
                                for f in entry_fill_meta],
                "note": "fill_ts on Alpaca intraday fills is the local trigger time (bar start or quote time); "
                        "broker_filled_at is Alpaca's own time when known.",
            },
            "stops": {
                "structural_stop": rnd(features.get("structural_stop")),
                "floored_signal_stop": signal.get("floored_stop"),
                "adapted_stop": rnd(stages.get("adapted_stop")),
                "stop_at_entry": initial_stop,
                "stop_multiplier": stages.get("stop_multiplier"),
                "final_stop": rnd(bracket.current_stop_price),
                "stop_moves": stop_moves,
                "stop_history": rs.get("stop_history"),
            },
            "risk": {
                "risk_per_share_at_fill": rnd(rps),
                "stop_beyond_fill": stop_beyond_fill,
                "initial_risk_dollars": rnd(initial_risk, 2),
                "entry_slippage_vs_signal": rnd(entry_slip),
                "entry_slippage_r": rnd(entry_slip / rps, 3) if entry_slip is not None and rps else None,
                "signal_entry_note": "signal entry = close of the signal bar; the market order fills after it",
            },
            "targets": {
                "signal_target_1": signal.get("target_1"),
                "signal_target_2": signal.get("target_2"),
                "target_1_is_r_fallback": signal.get("target_1_is_r_fallback"),
                "target_2_is_r_fallback": signal.get("target_2_is_r_fallback"),
                "override_target_1": stages.get("target_1_override"),
                "override_target_2": stages.get("target_2_override"),
                "effective_target_1": effective_t1,
                "effective_target_2": effective_t2,
                "effective_target_1_r": r_of(effective_t1),
                "effective_target_2_r": r_of(effective_t2),
                "configured_target_1_r": activation.get("target_1_r_config"),
                "configured_target_2_r": activation.get("target_2_r_config"),
                "planned_target_1_qty": activation.get("target_1_qty"),
                "planned_target_2_qty": activation.get("target_2_qty"),
                "trailing_enabled": activation.get("trailing_enabled"),
                "trail_atr_multiplier": activation.get("trail_atr_multiplier"),
            },
            "quantities": {
                "admission_qty": stages.get("admission_qty"),
                "risk_authorized_qty": stages.get("risk_authorized_qty"),
                "submitted_qty": rs.get("submitted_qty"),
                "entry_filled_qty": entry_qty,
                "exited_qty": exit_qty,
                "balanced": entry_qty == exit_qty,
            },
            "prices": {"avg_entry": rnd(avg_entry), "avg_exit": rnd(avg_exit)},
            "result": {
                "realized_pnl_gross": rnd(pnl, 2),
                "fees_booked": trade.get("fees"),
                "realized_r": rnd(pnl / initial_risk, 3) if initial_risk else None,
            },
            "excursion": excursion,
            "exit_fills": exits,
            "signal": signal or None,
            "stages": stages or None,
            "context": rs.get("context"),
            "late_start_reason": rs.get("late_start_reason"),
            "ledger_row": trade,
        }
        self.recorder.record("trades", row_id, row)
        return row

    # ------------------------------------------------------------------ swing
    def _staged(self, symbol: str, action: str) -> Optional[Any]:
        getter = self.swing_staged.get_staged_entries if action == "BUY" else self.swing_staged.get_staged_exits
        for staged in getter():
            if staged.symbol.upper() == symbol:
                return staged
        return None

    def _swing_fill(self, order: Any, fill: Any, meta: Dict[str, Any]) -> None:
        sym = order.symbol.upper()
        acc = self.state["swing"].get(sym)
        side = meta["side"]
        if acc is None:
            staged = self._staged(sym, "BUY") if side == "BUY" else None
            acc = {
                "round_trip_id": f"swing_{sym}_{meta['fill_ts']}",
                "started_with_trade": side == "BUY",
                "late_start_reason": None if side == "BUY" else "sell fill without a recorded entry",
                "scan": json_safe(staged.to_dict()) if staged is not None and hasattr(staged, "to_dict") else None,
                "entry_atr": rnd(getattr(order, "swing_entry_atr", None)),
                "stop": None,
                "context": self.context(self.swing_id, sym, fill.timestamp),
                "entry_fills": [], "exit_fills": [],
                "excursion": new_excursion(),
            }
            self.state["swing"][sym] = acc
        if side == "BUY":
            acc["entry_fills"].append(meta)
        else:
            staged = self._staged(sym, "SELL")
            meta["exit_intent"] = (f"staged: {getattr(staged, 'reason', None)}" if staged is not None
                                   else "UNSTAGED (emergency stop or operator)")
            meta["exit_intent_note"] = "staged label read at fill time; an emergency exit while a staged exit exists shows the staged label"
            pos = self.account.positions.get(sym)
            meta["stop_in_force"] = rnd(getattr(pos, "stop_loss_price", None)) if pos is not None else acc.get("stop")
            acc["exit_fills"].append(meta)
        fold_price(acc["excursion"], fill.price, fill.timestamp)
        pos = self.account.positions.get(sym)
        if side != "BUY" and (pos is None or int(getattr(pos, "shares", 0) or 0) == 0):
            self._complete_swing(sym, acc)

    def _complete_swing(self, sym: str, acc: Dict[str, Any]) -> None:
        self.state["swing"].pop(sym, None)
        mode = self.execution_mode()
        entries, exits = acc["entry_fills"], acc["exit_fills"]
        eq = sum(f["qty"] for f in entries)
        xq = sum(f["qty"] for f in exits)
        avg_entry = sum(f["qty"] * f["price"] for f in entries) / eq if eq else None
        avg_exit = sum(f["qty"] * f["price"] for f in exits) / xq if xq else None
        pnl = sum(float(f.get("realized_pnl") or 0.0) for f in exits)
        stop, stop_source = acc.get("stop"), acc.get("stop_source")
        if stop is None:
            stop = next((f.get("stop_in_force") for f in exits if f.get("stop_in_force")), None)
            stop_source = "position stop at exit" if stop is not None else None
        if stop is None and (acc.get("scan") or {}).get("stop_loss_price"):
            stop, stop_source = rnd(acc["scan"]["stop_loss_price"]), "staged scan stop"
        if stop is None and acc.get("entry_atr") and avg_entry:
            stop, stop_source = rnd(avg_entry - 2.5 * acc["entry_atr"]), "fill - 2.5 x entry ATR (rule 6)"
        rps = (avg_entry - stop) if avg_entry is not None and stop is not None and avg_entry > stop else None
        entry_at = min((parse_ts(f["fill_ts"]) for f in entries), default=None)
        exit_at = max((parse_ts(f["fill_ts"]) for f in exits), default=None)
        exc = excursion_summary(acc["excursion"], "LONG", avg_entry or 0.0, rps, entry_at, exit_at,
                                bool(acc.get("started_with_trade")), session_gaps_only=True)
        exc["coverage_notes"].append("swing holds overnight; only regular-session minute bars are seen")
        session = entry_at.astimezone(ET).date().isoformat() if entry_at else datetime.now(ET).date().isoformat()
        row_id = f"{self.mode_tag()}:{acc['round_trip_id']}"
        row = {
            "row_id": row_id,
            "kind": "SWING",
            "complete": bool(entries) and bool(exits),
            "execution_mode": mode,
            "code_revision": self.code_revision(),
            "fees_known": mode != "alpaca_paper",
            "session_date": session,
            "strategy_id": self.swing_id,
            "symbol": sym,
            "side": "LONG",
            "opened_at": iso(entry_at),
            "closed_at": iso(exit_at),
            "hold_days": rnd((exit_at - entry_at).total_seconds() / 86400.0, 3) if entry_at and exit_at else None,
            "scan": acc.get("scan"),
            "context_note": "context captured at the entry fill (09:30), not at the 16:00 scan that decided it",
            "entry_atr": acc.get("entry_atr"),
            "stops": {"stop_at_entry": rnd(stop), "stop_source": stop_source},
            "risk": {"risk_per_share_at_fill": rnd(rps), "initial_risk_dollars": rnd(rps * eq, 2) if rps and eq else None},
            "quantities": {"entry_filled_qty": eq, "exited_qty": xq, "balanced": eq == xq},
            "prices": {"avg_entry": rnd(avg_entry), "avg_exit": rnd(avg_exit)},
            "result": {"realized_pnl_gross": rnd(pnl, 2),
                       "realized_r": rnd(pnl / (rps * eq), 3) if rps and eq else None},
            "excursion": exc,
            "entry_fills": entries,
            "exit_fills": exits,
            "context": acc.get("context"),
            "late_start_reason": acc.get("late_start_reason"),
        }
        self.recorder.record("trades", row_id, row)
