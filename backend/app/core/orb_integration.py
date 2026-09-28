"""ADT <-> ORB glue (phase 3 of PLAN_2026_09_28_orb_rules_match_orbstraddle.md, sections 2-5, 8, 9).

ORB (strategy id "orb") trades ORBStraddle's rules through its own controller
(core/orb_execution.py) and scheduler (core/orb_scheduler.py). This module wires them into the
running app (`runtime` = backend.app.main):

  * one reservation lock (`self.lock`) shared by ORB and every other ADT arm: ORB takes a symbol
    only if no ADT arm holds / has a working order / has a staged swing entry / has an entry in
    flight on it, and ADT arms refuse ("ORB_OWNED") a symbol ORB holds or reserved;
  * ORB's broker fills are booked into ADT's account and ledger on the event loop (idempotent,
    from the controller's cumulative per-order fills), so ADT's Alpaca position check stays true
    and trades land in /api/trades with strategy_id "orb";
  * ADT's daily loss stop halts ORB (account_halt) and ORB's open risk is reserved out of ADT's
    remaining daily-loss budget (risk_engine.reserved_risk_fn);
  * every ADT exit path hands ORB symbols to request_exit / request_all_exits, which cancel ORB's
    own parent and legs before closing exactly ORB's quantity;
  * the controller and scheduler state are their own durable rows (orb_state table), written by
    ORB's worker threads before they return; the ADT runtime checkpoint carries only the ledger
    linkage (which ORB fills are booked, which trades are recorded).

Threading: controller/scheduler work runs on the scheduler's worker threads. The hooks called from
those threads (persist, on_fill, is_occupied, reserve, release, account_halt, entry_gate, on_event)
never mutate ADT's account/engine: fills are booked by `sync()` on the event loop.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import threading
import time as _time
from collections import deque
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.app.core.engine import ORB_POLICY, OrderSide, OrderType

log = logging.getLogger("orb_integration")
ET = ZoneInfo("America/New_York")
ORB_ID = "orb"
MODES = ("off", "shadow", "live")
LEDGER_VERSION = 1
ACTIVE = frozenset({"PENDING_SUBMIT", "SUBMITTED", "UNKNOWN", "OPEN"})
CLAIM_STATES = frozenset({"CREATED", "SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED"})
SYNC_EVERY_S = 5.0


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


class OrbIntegration:
    def __init__(self, runtime: Any) -> None:
        self.r = runtime
        self.controller = None
        self.scheduler = None
        self.facade = None
        self.mode = "off"
        self.configured_mode = "off"
        self.lock = threading.RLock()            # THE shared symbol-reservation lock
        self._orb_reserved: set = set()
        self._adt_claims: Dict[str, str] = {}    # symbol -> local order id of an ADT entry in flight
        self.ledger: Dict[str, Any] = self._empty_ledger()
        self._mem_state: Dict[str, Any] = {}     # ORB state when there is no durable store
        self._dirty = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._last_sync = 0.0
        self.errors: deque = deque(maxlen=20)
        self.events: deque = deque(maxlen=50)
        self.init_error: Optional[str] = None
        self.clock: Callable[[], datetime] = lambda: datetime.now(ET)

    @staticmethod
    def _empty_ledger() -> Dict[str, Any]:
        # booked: ORB order record key -> {qty, notional, local}; recorded: position keys with a trade row;
        # logged: decision rows already written (dedupe across restarts)
        return {"version": LEDGER_VERSION, "booked": {}, "recorded": [], "logged": []}

    # ------------------------------------------------------------------ construction
    def effective_manifest(self) -> dict:
        """PARITY_MANIFEST.json with ADT's TSLA/CDE exclusion applied (the manifest keeps [] for parity)."""
        from backend.app.strategies.orbs import config as orbs_config
        manifest = copy.deepcopy(orbs_config.load_manifest())
        manifest.setdefault("adt", {})["exclude_symbols"] = [
            str(s).upper() for s in self.r.settings.ORB_EXCLUDE_SYMBOLS]
        return manifest

    def state_dir(self) -> str:
        s = self.r.settings
        if s.ORB_STATE_DIR:
            return s.ORB_STATE_DIR
        return str(Path(s.STATE_DB_PATH).expanduser().resolve().parent / "orbs")

    def build(self, broker: Any, facade: Any, mode: str, *, clock: Optional[Callable[[], datetime]] = None,
              inline: bool = False, manifest: Optional[dict] = None, deadlines: Optional[dict] = None,
              monotonic: Optional[Callable[[], float]] = None, budget: Any = None,
              sleep: Optional[Callable[[float], None]] = None, is_session: Optional[Callable] = None,
              restore: bool = True, expected_account: Optional[str] = None):
        """Construct the controller + scheduler with ADT's hooks, restoring their durable state."""
        from backend.app.core.orb_execution import OrbExecutionController
        from backend.app.core.orb_scheduler import OrbScheduler
        if mode not in MODES:
            raise ValueError(f"ORB_MODE must be one of {MODES}, not {mode!r}")
        self.shutdown()
        manifest = manifest if manifest is not None else self.effective_manifest()
        ctl = OrbExecutionController(
            broker, facade, manifest, clock=clock, persist_cb=self._persister("controller"),
            on_fill=self._on_fill, is_occupied=self.is_occupied, reserve=self.reserve, release=self.release,
            account_halt=self.account_halt, on_event=self._on_event, sleep=sleep, budget=budget, mode=mode,
            expected_account=expected_account or self.r.settings.ORB_EXPECTED_ACCOUNT,
            entry_gate=self.entry_gate)
        sched = OrbScheduler(
            ctl, facade, manifest, clock=clock, is_session=is_session or self._is_session,
            persist_cb=self._persister("scheduler"), inline=inline, deadlines=deadlines, monotonic=monotonic,
            on_decision=self._on_decision)
        if restore:
            ctl.from_state(self._load("controller"))
            sched.from_state(self._load("scheduler"))
        with self.lock:
            self._orb_reserved.clear()
        self.controller, self.scheduler, self.facade, self.mode = ctl, sched, facade, mode
        self.clock = clock or (lambda: datetime.now(ET))
        self._dirty.set()
        return ctl, sched

    def start(self) -> None:
        """Lifespan startup: the real facade (relay) and, in alpaca_paper mode, ADT's Alpaca broker."""
        s = self.r.settings
        self.configured_mode = mode = str(s.ORB_MODE or "off").strip().lower()
        if mode not in MODES:
            self.init_error = f"ORB_MODE {s.ORB_MODE!r} is not off/shadow/live; ORB is off"
            log.error(self.init_error)
            mode = "off"
        broker = self.r.alpaca_broker if s.BROKER_MODE.lower() == "alpaca_paper" else None
        if broker is None and mode == "live":
            self.init_error = "ORB_MODE=live needs BROKER_MODE=alpaca_paper; running ORB in shadow with no broker"
            log.warning(self.init_error)
            mode = "shadow"
        facade = None
        if mode != "off" or broker is not None:
            try:
                from backend.app.strategies.orbs.facade import OrbsFacade
                facade = OrbsFacade(self.state_dir(), s.RELAY_HTTP_URL, s.RELAY_TOKEN,
                                    manifest=self.effective_manifest())
                facade.set_exclude_symbols(s.ORB_EXCLUDE_SYMBOLS)
            except Exception as exc:
                self.init_error = f"ORB decision code could not start ({type(exc).__name__}: {exc}); ORB is off"
                log.exception("ORB facade construction failed")
                facade, mode = None, "off"
        self.build(broker, facade, mode)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        log.info("ORB (ORBStraddle rules) started: mode=%s broker=%s", mode, "alpaca_paper" if broker else "none")

    def shutdown(self) -> None:
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown()
            except Exception:
                log.exception("ORB scheduler shutdown failed")
        self.scheduler = None
        self.controller = None

    def reset(self) -> None:
        """Test/replay isolation (reset_runtime_state)."""
        self.shutdown()
        self.facade = None
        self.mode = "off"
        self.ledger = self._empty_ledger()
        self._mem_state.clear()
        with self.lock:
            self._orb_reserved.clear()
            self._adt_claims.clear()
        self.errors.clear()
        self.events.clear()
        self.init_error = None

    def _is_session(self, d: date) -> Optional[bool]:
        return self.r.is_trading_day(d)

    # ------------------------------------------------------------------ durable ORB state
    def _persister(self, section: str) -> Callable[[dict], None]:
        def persist(state: dict) -> None:
            store = self.r.state_store
            if store is not None and not self.r.simulation_mode:
                store.save_orb_state(section, state)       # durable before return; raises on failure
            else:
                self._mem_state[section] = copy.deepcopy(state)
        return persist

    def _load(self, section: str) -> Optional[dict]:
        store = self.r.state_store
        if store is not None:
            return store.load_orb_state(section)
        return copy.deepcopy(self._mem_state.get(section))

    def ledger_state(self) -> Dict[str, Any]:
        return copy.deepcopy(self.ledger)

    def load_ledger_state(self, state: Optional[dict]) -> None:
        base = self._empty_ledger()
        if isinstance(state, dict) and state.get("version") == LEDGER_VERSION:
            base.update({k: copy.deepcopy(state.get(k, base[k])) for k in ("booked", "recorded", "logged")})
        self.ledger = base

    # ------------------------------------------------------------------ reservations (one lock)
    def adt_occupied(self, symbol: str) -> Optional[str]:
        """Why another ADT arm has this symbol, or None. Never counts ORB itself."""
        r, sym = self.r, symbol.upper()
        pos = r.account.positions.get(sym)
        if pos is not None and getattr(pos, "strategy_id", "") != ORB_ID:
            return f"ADT holds {sym} ({getattr(pos, 'strategy_id', '')})"
        for order in list(r.engine.working_orders.values()):
            if order.symbol.upper() == sym and order.execution_policy != ORB_POLICY:
                return f"ADT has a working order on {sym}"
        oid = self._adt_claims.get(sym)
        if oid is not None:
            order = r.engine.orders.get(oid)
            if order is not None and order.status.value in CLAIM_STATES:
                return f"an ADT entry on {sym} is in flight"
            self._adt_claims.pop(sym, None)
        if sym in r.bracket_manager.symbol_to_bracket:
            return f"ADT has a bracket on {sym}"
        if sym in r.swing_reserved_symbols or r.swing_staged_order_manager.is_staged_for_entry(sym):
            return f"{sym} is reserved or staged for the swing arm"
        if r.tri_controller.reserves(sym):
            return f"{sym} belongs to the Tesla/Coeur morning plan today"
        if r.or15_controller.reserves(sym):
            return f"{sym} belongs to the OR15 plan"
        return None

    def is_occupied(self, symbol: str) -> bool:
        with self.lock:
            return self.adt_occupied(symbol) is not None

    def reserve(self, symbol: str) -> bool:
        sym = symbol.upper()
        with self.lock:
            if self.adt_occupied(sym) is not None:
                return False
            self._orb_reserved.add(sym)
            return True

    def release(self, symbol: str) -> None:
        with self.lock:
            self._orb_reserved.discard(symbol.upper())

    def owns(self, symbol: str) -> bool:
        """ORB holds, reserved, or has a live order on symbol (or ADT's book says an ORB position is there)."""
        sym = symbol.upper()
        pos = self.r.account.positions.get(sym)
        if pos is not None and getattr(pos, "strategy_id", "") == ORB_ID:
            return True
        with self.lock:
            if sym in self._orb_reserved:
                return True
        ctl = self.controller
        return bool(ctl is not None and ctl.owns(sym))

    def claim_for_adt(self, symbol: str, order: Any, strategy_id: Optional[str]) -> Optional[str]:
        """ADT entry admission (pre-trade validator), atomically with ORB's reserve(): refuse a symbol ORB
        owns, else record the in-flight entry so ORB cannot take the symbol meanwhile."""
        sym = symbol.upper()
        with self.lock:
            if strategy_id != ORB_ID and self.owns(sym):
                return (f"ORB_OWNED: {sym} is held or reserved by the Opening Range Breakout (ORBStraddle "
                        "rules); other strategies cannot trade it until ORB is done with it")
            if order is not None and getattr(order, "id", None):
                self._adt_claims[sym] = order.id
        return None

    def symbols(self) -> List[str]:
        out = {s for s, p in self.r.account.positions.items() if getattr(p, "strategy_id", "") == ORB_ID}
        with self.lock:
            out |= set(self._orb_reserved)
        ctl = self.controller
        if ctl is not None:
            out |= set(ctl.reserved_symbols()) | set(ctl._own_open_symbols())
        return sorted(out)

    # ------------------------------------------------------------------ hooks called by ORB's threads
    def account_halt(self) -> Optional[str]:
        r = self.r
        if r.risk_engine.status != r.BreakerStatus.ARMED:
            return "ADT's daily loss stop was hit"
        if r.account.status.value == "CIRCUIT_HALTED":
            return "ADT's circuit breaker halted the account"
        return None

    def entry_gate(self, symbol: str) -> Optional[str]:
        """ADT's _broker_gate for ORB entries: regular hours only, no broker position mismatch, and a
        healthy durable ledger. Exits never pass through here."""
        r = self.r
        if r.simulation_mode:
            return "replay mode never sends real orders"
        now_et = self.clock().astimezone(ET)
        today = now_et.date()
        if not r.is_trading_day(today) or not (time(9, 30) <= now_et.time() < r.session_close(today)):
            return "MARKET_CLOSED: real orders only go out during regular hours"
        if r.broker_state.get("mismatch"):
            return "BROKER_MISMATCH: bot and Alpaca positions differ; entries paused"
        if r.state_store is not None and not r.persistence_healthy:
            return "PERSISTENCE_RECOVERY_HALT: the durable ledger is unavailable"
        return None

    def _on_fill(self, symbol, side, qty, price, order_id, role) -> None:
        self._dirty.set()
        self._wake()

    def _on_event(self, row: dict) -> None:
        self.events.append(dict(row))
        if row.get("kind") in ("own_fill", "position_closed", "position_reopened", "position_rebuilt",
                               "execution_intent", "exit_requested", "exit_all_requested"):
            self._dirty.set()
        if row.get("kind") in ("alarm", "supervision_symbol_error"):
            self.errors.append({k: row.get(k) for k in ("ts", "kind", "alarm", "symbol", "err", "reason", "note")})

    def _wake(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._loop_sync)
        except RuntimeError:
            pass  # loop closed (shutdown)

    def _loop_sync(self) -> None:
        try:
            if self.sync(datetime.now(timezone.utc)):
                self.r._checkpoint_runtime("ORB_FILL")
        except Exception as exc:
            log.exception("ORB ledger sync failed")
            self.errors.append({"kind": "ledger_sync", "err": str(exc)[:200]})

    # ------------------------------------------------------------------ event-loop side
    def tick(self, now: datetime) -> None:
        """Called ~1/s from ADT's runtime clock loop. Never blocks: the scheduler only starts and
        collects worker jobs; the ledger sync is in-memory."""
        if self.scheduler is None or self.r.simulation_mode:
            return
        try:
            self.scheduler.tick(now)
        except Exception as exc:
            log.exception("ORB scheduler tick failed")
            self.errors.append({"kind": "scheduler_tick", "err": str(exc)[:200]})
        mono = _time.monotonic()
        if self._dirty.is_set() or mono - self._last_sync >= SYNC_EVERY_S:
            self._last_sync = mono
            try:
                if self.sync(now):
                    self.r._checkpoint_runtime("ORB_SYNC")
            except Exception as exc:
                log.exception("ORB ledger sync failed")
                self.errors.append({"kind": "ledger_sync", "err": str(exc)[:200]})

    def _local_order(self, key: str, rec: dict) -> Any:
        r = self.r
        booked = self.ledger["booked"].get(key) or {}
        local = r.engine.orders.get(booked.get("local") or "")
        if local is not None:
            return local
        side = OrderSide.BUY if rec.get("side") == "buy" else OrderSide.SELL
        qty = max(int(rec.get("qty") or 0), int(rec.get("filled_qty") or 0), 1)
        role = rec.get("role")
        name = ("target" if rec.get("leg_kind") == "tp" else "stop") if role == "leg" else role
        local = r.engine.create_order(
            symbol=rec["symbol"], side=side, order_type=OrderType.MARKET, qty=qty, strategy_id=ORB_ID,
            client_order_id=str(rec.get("coid") or rec.get("id") or key),
            parent_order_id=f"orb:{rec.get('position') or key}", arm=r.TradingArm.INTRADAY)
        local.execution_policy = ORB_POLICY
        local.reject_reason = None
        local.estimated_price = _f(rec.get("avg_price"))
        r.engine._record_audit(local, local.status, "ORB_BROKER_ORDER",
                               f"ORB {name} order {rec.get('id') or rec.get('coid')} at Alpaca")
        return local

    def sync(self, now: Optional[datetime] = None) -> bool:
        """Book ORB's new broker fills into ADT's account/ledger (idempotent, cumulative per ORB order),
        refresh the ORB positions' prices, record finished ORB trades. Event loop only."""
        ctl = self.controller
        self._dirty.clear()
        if ctl is None:
            return False
        r = self.r
        now = now or datetime.now(timezone.utc)
        st = ctl.to_state()
        changed = False
        booked = self.ledger["booked"]
        for key, rec in sorted(st.get("orders", {}).items(), key=lambda kv: (kv[1].get("fill_ts") or 0, kv[0])):
            filled = int(rec.get("filled_qty") or 0)
            avg = _f(rec.get("avg_price"))
            b = booked.get(key) or {"qty": 0, "notional": 0.0, "local": None}
            delta = filled - int(b["qty"])
            if delta <= 0 or avg is None or avg <= 0:
                continue
            notional = filled * avg
            px = (notional - float(b["notional"])) / delta
            if not math.isfinite(px) or px <= 0:
                px = avg
            local = self._local_order(key, rec)
            if local.remaining_qty < delta:
                local.qty += delta - local.remaining_qty
                local.remaining_qty = delta
            ts = rec.get("fill_ts")
            at = datetime.fromtimestamp(float(ts), timezone.utc) if ts else now
            r.engine._apply_fill_to_ledger(local, delta, round(px, 6), 0.0, 0.0, at)
            booked[key] = {"qty": filled, "notional": notional, "local": local.id, "symbol": rec["symbol"],
                           "position": rec.get("position"), "role": rec.get("role"), "leg_kind": rec.get("leg_kind")}
            changed = True
            log.info("ORB fill booked: %s %s %d @ %.4f (%s)", rec.get("side"), rec["symbol"], delta, px, rec.get("role"))
        # prices for ORB positions ADT does not stream, so ADT's daily loss stop sees ORB's P&L
        for pos in st.get("positions", {}).values():
            if pos.get("status") not in ACTIVE:
                continue
            sym, px = pos["symbol"], _f(pos.get("last_px"))
            if px and px > 0 and sym in r.account.positions and sym not in r.latest_market_prices:
                r.account.update_market_price(sym, px)
        changed |= self._complete_trades(st, now)
        if changed and r.risk_engine.evaluate_account_state(
                equity=r.account.equity, cash=r.account.cash, realized_pnl=r.account.realized_pnl,
                unrealized_pnl=r.account.unrealized_pnl, timestamp=now) == r.BreakerStatus.HALTED_DAILY_LOSS:
            if r.account.status.value != "CIRCUIT_HALTED":
                r._trip_circuit_breaker(now)
        return changed

    def _complete_trades(self, st: dict, now: datetime) -> bool:
        r = self.r
        recorded = set(self.ledger["recorded"])
        own = st.get("own", {})
        changed = False
        for key, pos in st.get("positions", {}).items():
            if key in recorded or pos.get("status") in ACTIVE or pos.get("status") == "SHADOW":
                continue
            sym = pos["symbol"]
            if int((own.get(sym) or {}).get("qty") or 0) != 0:
                continue                         # shares still open under another position row
            recs = {k: v for k, v in st.get("orders", {}).items() if (v.get("position") or k) == key}
            if any(int(v.get("filled_qty") or 0) != int((self.ledger["booked"].get(k) or {}).get("qty") or 0)
                   for k, v in recs.items()):
                continue                         # a fill is not booked yet
            locals_ = [r.engine.orders.get((self.ledger["booked"].get(k) or {}).get("local") or "") for k in recs]
            entries = [o for k, o in zip(recs, locals_) if o is not None and recs[k].get("role") == "entry"]
            exits = [o for k, o in zip(recs, locals_) if o is not None and recs[k].get("role") != "entry"]
            entry_fills = [f for o in entries for f in o.fills]
            exit_fills = [f for o in exits for f in o.fills]
            self.ledger["recorded"].append(key)
            self.ledger["recorded"] = self.ledger["recorded"][-500:]
            changed = True
            if not entry_fills:
                continue                         # never traded (rejected, skipped, never filled)
            qty = sum(f.qty for f in entry_fills)
            exit_qty = sum(f.qty for f in exit_fills)
            pnl = round(sum(f.realized_pnl for f in exit_fills), 2)
            avg_in = sum(f.qty * f.price for f in entry_fills) / qty
            avg_out = (sum(f.qty * f.price for f in exit_fills) / exit_qty) if exit_qty else None
            opened = min(f.timestamp for f in entry_fills)
            closed = max(f.timestamp for f in exit_fills) if exit_fills else now
            trade = {
                "trade_id": f"orb_{key}", "session_date": str(pos.get("day") or opened.astimezone(ET).date()),
                "symbol": sym, "side": "LONG" if pos.get("direction") == "long" else "SHORT", "status": "CLOSED",
                "strategy_id": ORB_ID, "opened_at": opened.isoformat(), "closed_at": closed.isoformat(),
                "quantity": qty, "avg_entry_price": round(avg_in, 4),
                "avg_exit_price": round(avg_out, 4) if avg_out else None, "realized_pnl": pnl, "fees": 0.0,
                "broker_fees": None, "exit_reason": pos.get("closed_reason") or pos.get("exit_reason"),
                "aggregate_only": False, "execution_mode": "alpaca_paper", "rules": "ORBStraddle adaptive-v1.6.0-flow-rules",
                "orb": {k: pos.get(k) for k in ("coid", "tier", "wave", "entry_ref", "card_entry", "stop", "initial_stop",
                                                 "target", "rd", "risk_usd", "planned_shares", "peak", "be_locked",
                                                 "exit_reason", "closed_reason", "carried")},
                "r_multiple": round(pnl / float(pos["risk_usd"]), 3) if _f(pos.get("risk_usd")) else None,
                "fill_legs": [{"fill_id": f.fill_id, "order_id": f.order_id, "side": f.side.value, "qty": f.qty,
                               "price": f.price, "fee": f.fee, "realized_pnl": f.realized_pnl,
                               "timestamp": f.timestamp.isoformat()} for f in entry_fills + exit_fills],
            }
            trade = r._sanitize_for_json(trade)
            r.pending_trade_records[trade["trade_id"]] = trade
            strat = r.strategy_map.get(ORB_ID)
            if strat is not None:
                strat.record_trade(pnl)
            if r.settings.RESEARCH_ENABLED:
                r.research_safe(r.research_recorder.record, "trades", trade["trade_id"], trade,
                                recorder=r.research_recorder)
            log.info("ORB trade closed: %s %s pnl %.2f (%s)", sym, trade["side"], pnl, trade["exit_reason"])
        return changed

    # ------------------------------------------------------------------ decisions log / research
    def _on_decision(self, row: dict) -> None:
        """Scheduler hook (tick thread = event loop in production): decisions log + research rows."""
        r = self.r
        key = f"{row.get('kind')}|{row.get('wave')}|{row.get('at')}"
        if key in self.ledger["logged"]:
            return
        self.ledger["logged"].append(key)
        self.ledger["logged"] = self.ledger["logged"][-300:]
        at = datetime.fromisoformat(row["at"]) if row.get("at") else datetime.now(timezone.utc)
        day = at.astimezone(ET).date().isoformat()
        mode = self.controller.mode if self.controller is not None else self.mode
        if row.get("kind") == "verdict":
            picks = row.get("pick_details") or []
            if not picks:
                outcome = "ORB_SAT_OUT" if row.get("verdict") in ("sit_out", "pass", "blocked", "skipped") else "ORB_NO_DECISION"
                r.decision_log.record(ORB_ID, "BOARD", "-", 0.0, outcome,
                                      f"{row.get('wave')} decision: {row.get('reason') or row.get('verdict')}", at)
            elif not row.get("executing"):
                for p in picks:
                    r.decision_log.record(ORB_ID, str(p.get("symbol")), "BUY" if p.get("direction") == "long" else "SELL",
                                          _f(p.get("entry")) or 0.0, "ORB_NOT_EXECUTED", str(row.get("reason")), at)
            if r.settings.RESEARCH_ENABLED:
                audit = [{k: a.get(k) for k in ("symbol", "direction", "tier", "verdict", "reason", "chosen", "ev_dollars",
                                                 "win_prob", "sector", "stop_pct", "spread_bps", "rvol", "atr_pct")
                          if k in a} for a in (row.get("audit") or []) if isinstance(a, dict)][:80]
                rec = {"row_id": f"orb:{day}:{row.get('wave')}:{row.get('at')}", "session_date": day,
                       "strategy_id": ORB_ID, "kind": "orb_decision", "mode": mode, "wave": row.get("wave"),
                       "verdict": row.get("verdict"), "reason": row.get("reason"), "at": row.get("at"),
                       "picks": picks, "refused_picks": row.get("refused_picks") or [],
                       "board_id": row.get("board_id"), "board_symbols": row.get("board_symbols") or [],
                       "regime": row.get("regime") or {}, "audit": audit}
                r.research_safe(r.research_recorder.record, "setups", rec["row_id"], rec, recorder=r.research_recorder)
            return
        if row.get("kind") == "execution":
            for p in row.get("placed") or []:
                side = "BUY" if p.get("direction") == "long" else "SELL"
                outcome = {"accepted": "SUBMITTED", "shadow": "ORB_SHADOW", "unknown": "ORB_UNKNOWN"}.get(
                    p.get("outcome"), "ORB_REFUSED")
                if outcome == "ORB_SHADOW":
                    detail = (f"would {'buy' if side == 'BUY' else 'sell short'} {p.get('shares')} at ~{p.get('entry_ref')}, "
                              f"stop {p.get('stop')}, target {p.get('target')} (risk ${p.get('risk_usd')})")
                elif outcome == "SUBMITTED":
                    detail = (f"{side} {p.get('shares')} {p.get('symbol')} bracket: stop {p.get('stop')}, "
                              f"target {p.get('target')} (risk ${p.get('risk_usd')})")
                else:
                    detail = str(p.get("error") or p.get("outcome"))
                r.decision_log.record(ORB_ID, str(p.get("symbol")), side, _f(p.get("entry_ref")) or 0.0, outcome, detail, at)
            for sym, why in row.get("refused") or []:
                r.decision_log.record(ORB_ID, str(sym), "-", 0.0, "ORB_REFUSED", str(why), at)
            if not (row.get("placed") or row.get("refused")) and not row.get("ok"):
                r.decision_log.record(ORB_ID, "BOARD", "-", 0.0, "ORB_REFUSED", str(row.get("reason")), at)
            if r.settings.RESEARCH_ENABLED:
                rec = {"row_id": f"orb-exec:{day}:{row.get('wave')}:{row.get('at')}", "session_date": day,
                       "strategy_id": ORB_ID, "kind": "orb_execution", "mode": row.get("mode"), "wave": row.get("wave"),
                       "at": row.get("at"), "ok": row.get("ok"), "reason": row.get("reason"),
                       "placed": row.get("placed") or [], "refused": row.get("refused") or []}
                r.research_safe(r.research_recorder.record, "signals", rec["row_id"], rec, recorder=r.research_recorder)

    # ------------------------------------------------------------------ exit dispatch (ADT paths)
    def request_exit(self, symbol: str, reason: str) -> bool:
        """An ADT path (manual flatten, news exit, ...) wants ORB's position in symbol closed. The
        controller cancels ORB's parent + legs first, then closes exactly ORB's quantity."""
        ctl = self.controller
        if ctl is None:
            return False
        ok = ctl.request_exit(symbol, reason)
        if ok and self.scheduler is not None:
            self.scheduler.kick_supervisor()
        return ok

    def request_all_exits(self, reason: str, block_entries: bool = True) -> List[str]:
        ctl = self.controller
        if ctl is None:
            return []
        syms = set(ctl.reserved_symbols()) | set(ctl._own_open_symbols())
        with ctl._lock:
            pending = set(ctl.state["exit_requests"])
        # idempotent: the breaker path runs on every quote while halted; persist only on a change
        if not (syms - pending) and not (block_entries and not ctl.entries_blocked()):
            return sorted(syms)
        out = ctl.request_all_exits(reason, block_entries=block_entries)
        if out and self.scheduler is not None:
            self.scheduler.kick_supervisor()
        return out

    # ------------------------------------------------------------------ risk
    def open_risk(self) -> float:
        """ORB's open + pending risk: remaining shares x distance from the current price to the broker
        stop (planned shares for an entry not filled yet). No known stop = 7.5% of the notional."""
        ctl = self.controller
        if ctl is None:
            return 0.0
        total = 0.0
        for h in ctl.holdings():
            qty = abs(int(h.get("qty") or 0)) or int(h.get("planned_shares") or 0)
            px = _f(h.get("last_price")) or _f(h.get("avg_price")) or _f(h.get("entry_ref")) or 0.0
            stop = _f(h.get("stop"))
            if qty <= 0 or px <= 0:
                continue
            if stop is None:
                total += qty * px * 0.075
            else:
                total += qty * max(0.0, (px - stop) if h.get("direction") == "long" else (stop - px))
        return round(total, 2)

    # ------------------------------------------------------------------ views
    def status(self) -> Dict[str, Any]:
        ctl, sched = self.controller, self.scheduler
        out: Dict[str, Any] = {"mode": ctl.mode if ctl else self.mode, "configured_mode": self.configured_mode,
                               "rules": "ORBStraddle adaptive-v1.6.0-flow-rules (@71b001f)",
                               "exclude_symbols": list(self.r.settings.ORB_EXCLUDE_SYMBOLS),
                               "expected_account": self.r.settings.ORB_EXPECTED_ACCOUNT,
                               "init_error": self.init_error, "errors": list(self.errors)[-10:]}
        if ctl is None or sched is None:
            out.update(ready=False, step="ORB is not running.", last_verdict=None, picks=[], holdings=[])
            return out
        s = sched.status()
        c = ctl.status()
        out.update(ready=c.get("ready"), step=s.get("step"), next_step=s.get("next_step"), steps=s.get("steps"),
                   last_verdict=s.get("last_verdict"), picks=s.get("picks"), secondary=s.get("secondary"),
                   running=s.get("running"), session=s.get("session"), day=s.get("day"),
                   holdings=c.get("holdings"), halted=c.get("halted"), entries_blocked=c.get("entries_blocked"),
                   day_risk=c.get("day_risk"), slots=c.get("slots"), sizing=c.get("sizing"),
                   realized_today=c.get("realized_today"), startup=c.get("startup"), budget=c.get("budget"),
                   open_risk=self.open_risk(), last_execute=c.get("last_execute"))
        return out

    def card(self, now: datetime, strategy: Any, blockers: List[str]) -> Dict[str, Any]:
        from backend.app.core.trading_windows import orb_window
        card = strategy.to_dict()
        st = self.status()
        holdings = st.get("holdings") or []
        unreal = 0.0
        trades = []
        for h in holdings:
            pos = self.r.account.positions.get(h["symbol"])
            if pos is not None and getattr(pos, "strategy_id", "") == ORB_ID:
                unreal += pos.unrealized_pnl
            trades.append({k: h.get(k) for k in ("symbol", "direction", "qty", "avg_price", "stop", "target", "r",
                                                  "last_price", "breakeven_locked", "exit_requested", "status")})
        ctl = self.controller
        extra = list(blockers)
        if ctl is not None:
            if not ctl.ready:
                extra.append("Checking ORB's orders at the broker after a restart.")
            if ctl.halted():
                extra.append(f"ORB's own daily loss halt: {ctl.halted()}.")
            if ctl.entries_blocked():
                extra.append(f"No new ORB trades today ({ctl.entries_blocked()}).")
        mode = st.get("mode") or "off"
        card["window"] = orb_window(now, mode=mode, step_text=str(st.get("step") or ""), holding=bool(holdings),
                                    blockers=extra, operator_status=card.get("status", "ACTIVE"))
        card["orb"] = {
            "mode": mode,
            "mode_text": {"shadow": "Shadow: watching only, no orders", "live": "Live: paper account orders",
                          "off": "Off: no scans, no new trades"}.get(mode, mode),
            "step": st.get("step"), "next_step": st.get("next_step"), "ready": st.get("ready"),
            "last_verdict": st.get("last_verdict"), "picks": st.get("picks") or [],
            "open_trades": trades, "realized_pnl": round(float(strategy.daily_pnl or 0.0), 2),
            "unrealized_pnl": round(unreal, 2), "open_risk": st.get("open_risk"),
            "hours": "Decides 9:38 AM, may add trades until 10:15 AM, closes by 11:00 AM",
            "errors": st.get("errors") or [], "init_error": st.get("init_error"),
        }
        return card
