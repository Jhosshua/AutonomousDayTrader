"""ORB dry run: ADT's REAL app (backend.app.main: lifespan, runtime clock step, ORB controller + scheduler,
real OrbsFacade on ORBStraddle's copied decision code, ledger, breaker, EOD flatten, broker mismatch check,
checkpoints) on one recorded session, with a FAKE Alpaca that fills from the recorded SIP tape.

    python scripts/orb_dry_run/run_day.py --date 2026-09-28 --mode live --out DIR
        [--start 09:00:00] [--end 16:05:00] [--resume PREV_DIR] [--state DIR]
        [--stop-at HH:MM:SS --stop-kind hard|graceful] [--kill-on-post SYM] [--reconcile-every 30]

Never real orders, never real Alpaca, no network: every relay request is answered by store.DryRunTransport
from the recorded cache, and any socket connect raises. A simulated clock drives main._runtime_clock_step
(5 s steps 09:00-09:30, every second 09:30-11:05, 10 s steps after), the way main._runtime_clock_loop does
each second in production. ORB's worker jobs (scans, decisions, orders, the 5 s supervisor) run on their
real threads; the simulated clock waits for them (zero compute latency; their wall time is recorded).

Clock injection (harness only, no product code changed): ORB's controller/scheduler/integration get the
simulated clock through OrbIntegration.build(clock=, monotonic=, sleep=, budget=) exactly like the phase-3
tests; the decision shim's now_et() (unpinned) and orb_integration's throttle clock read the simulated time.
Outputs: DIR/result.json (records), DIR/broker.json (fake account), DIR/state (sqlite + ORB state)."""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import json
import os
import shutil
import socket
import sys
import threading
import time as _wall
import traceback
from datetime import date, datetime, time as dtime, timedelta, timezone
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
if REPO not in sys.path:
    sys.path.insert(0, REPO)

START_EQUITY = 49702.10
KEY_TIMES = ("09:37:00", "09:39:00", "10:00:00", "10:16:00", "11:01:00", "16:00:00")


# ---------------------------------------------------------------------------------------------- clock
class SimClock:
    def __init__(self, t: datetime):
        self._t = t
        self._lock = threading.Lock()

    @property
    def now(self) -> datetime:
        return self._t

    def __call__(self) -> datetime:
        return self._t

    def set(self, t: datetime) -> None:
        with self._lock:
            self._t = t

    def now_ns(self) -> int:
        t = self._t.astimezone(timezone.utc)
        return (int((t - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(seconds=1)) * 1_000_000_000
                + t.microsecond * 1000)

    def mono(self) -> float:
        return self._t.timestamp()


class _SimTimeModule:
    """Stands in for `time` inside orb_integration: monotonic() is the simulated clock (sync/mark throttles)."""

    def __init__(self, sim):
        self._sim = sim

    def monotonic(self):
        return self._sim.mono()

    def __getattr__(self, name):
        return getattr(_wall, name)


def hms(s: str) -> dtime:
    return dtime.fromisoformat(s)


# ---------------------------------------------------------------------------------------------- setup
def prepare_env(args, state_dir: str) -> None:
    for k in list(os.environ):
        if k.startswith(("ALPACA_", "APCA_", "ORBS_")) or k in ("BROKER_MODE", "RELAY_TOKEN"):
            del os.environ[k]
    os.environ.update({
        "ENV": "development",
        # a fresh day starts from an empty store (required=false lets lifespan write the first checkpoint);
        # a resumed segment restores it and requires it, like production
        "PERSISTENCE_ENABLED": "true", "PERSISTENCE_REQUIRED": "true" if args.resume else "false",
        "STATE_DB_PATH": os.path.join(state_dir, "trading_state.sqlite3"),
        "ORB_STATE_DIR": os.path.join(state_dir, "orbs"),
        "ORB_MODE": args.mode,
        # in-process only: lifespan builds its AlpacaBroker, which this harness points at the fake
        "BROKER_MODE": "alpaca_paper", "ALPACA_API_KEY": "dryrun-not-a-key", "ALPACA_SECRET_KEY": "dryrun-not-a-key",
        "START_RELAY_CLIENTS": "false", "RELAY_TOKEN": "",
        "INITIAL_CASH": str(START_EQUITY), "RESEARCH_ENABLED": "true",
    })


def block_network() -> None:
    def refuse(*a, **k):
        raise PermissionError("dry run: network access is forbidden")
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse


def slim_card(c):
    return {k: c.get(k) for k in ("symbol", "direction", "entry", "stop", "trig", "mode", "flow_blocked",
                                  "candle_blocked", "rvol", "atr_pct", "spread_bps", "gap_pct")}


class Recorder:
    def __init__(self, sim):
        self.sim = sim
        self.rows = []
        self.lock = threading.Lock()

    def add(self, kind, **kw):
        row = {"kind": kind, "sim": self.sim.now.astimezone(ET).isoformat(), **kw}
        with self.lock:
            self.rows.append(row)
        return row

    def of(self, kind):
        with self.lock:
            return [r for r in self.rows if r["kind"] == kind]


def instrument_facade(fac, rec, sim):
    """Record every facade call; each call goes to the real method unchanged."""
    import parity_common as pc
    o_scan, o_decide, o_recheck = fac.scan, fac.decide, fac.recheck
    o_macro, o_latest, o_abs = fac.macro_veto, fac.latest_trade, fac.absorption_poll

    def scan(day, end, wave, skip_symbols, executed_today=None):
        w0 = _wall.perf_counter()
        res = o_scan(day, end, wave, skip_symbols, executed_today=executed_today)
        rec.add("scan", wave=wave, end=end.astimezone(ET).strftime("%H:%M"), skip=sorted(skip_symbols or []),
                executed=sorted(executed_today or []), ok=res.get("ok"), error=res.get("error"),
                coverage=res.get("coverage"), board_id=res.get("board_id"), cards=res.get("cards") or [],
                health=pc.health_subset(res.get("health")), wall_s=round(_wall.perf_counter() - w0, 2))
        return res

    def decide(day, board, wave, now, occupied, executed_today=None):
        w0 = _wall.perf_counter()
        res = o_decide(day, board, wave, now, occupied, executed_today=executed_today)
        reg = res.get("regime") or {}
        rec.add("decide", wave=wave, now=now.astimezone(ET).isoformat(), board_id=(board or {}).get("board_id"),
                board_end=(board or {}).get("end"), occupied=sorted(occupied or []),
                executed=sorted(executed_today or []), verdict=res.get("verdict"), reason=res.get("reason"),
                picks=[{k: p.get(k) for k in ("symbol", "direction", "tier", "entry", "stop")} for p in res.get("picks") or []],
                refused=res.get("refused"), valid=res.get("valid"), validated=res.get("validated"),
                regime={k: reg.get(k) for k in ("classification", "action", "short_frac")},
                reply=res.get("reply"), note=res.get("note"),
                card_symbols=[c.get("symbol") for c in res.get("cards") or []],
                wall_s=round(_wall.perf_counter() - w0, 2))
        return res

    def recheck(card, now):
        res = o_recheck(card, now)
        rec.add("recheck", symbol=card.get("symbol"), now=now.astimezone(ET).isoformat(), ok=res[0], why=res[1])
        return res

    def macro_veto(symbol, direction, now):
        res = o_macro(symbol, direction, now)
        rec.add("macro_veto", symbol=symbol, direction=direction, now=now.astimezone(ET).isoformat(),
                vetoed=res[0], why=res[1])
        return res

    def latest_trade(symbol):
        res = o_latest(symbol)
        rec.add("latest_trade", symbol=symbol, price=(res or {}).get("price"),
                ts=str((res or {}).get("ts")), age_s=(res or {}).get("age_s"))
        return res

    def absorption_poll(symbol, direction, now):
        res = o_abs(symbol, direction, now)
        if res is not None:
            rec.add("absorption", symbol=symbol, direction=direction,
                    result={k: (str(v) if k == "asof" else v) for k, v in res.items()})
        return res

    fac.scan, fac.decide, fac.recheck = scan, decide, recheck
    fac.macro_veto, fac.latest_trade, fac.absorption_poll = macro_veto, latest_trade, absorption_poll


ET = None   # set in main (zoneinfo)


def main():
    global ET
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--mode", choices=("live", "shadow"), required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--state", default=None, help="state dir (sqlite + ORB rows); default OUT/state")
    ap.add_argument("--start", default="09:00:00")
    ap.add_argument("--end", default="16:05:00")
    ap.add_argument("--resume", default=None, help="previous segment's OUT (its broker.json is loaded)")
    ap.add_argument("--stop-at", default=None)
    ap.add_argument("--stop-kind", choices=("hard", "graceful"), default="hard")
    ap.add_argument("--kill-on-post", default=None)
    ap.add_argument("--reconcile-every", type=float, default=30.0)
    ap.add_argument("--no-preload", action="store_true")
    ap.add_argument("--macro-adapter", action="store_true",
                    help="flip OrbsFacade.macro_veto's answer into the (ok, why) the controller expects (BUG 1)")
    ap.add_argument("--relay-latency-ms", type=float, default=0.0,
                    help="sleep this long in every relay answer (network wait; releases the GIL like real I/O)")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    state_dir = args.state or os.path.join(args.out, "state")
    os.makedirs(state_dir, exist_ok=True)
    prepare_env(args, state_dir)

    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    day = date.fromisoformat(args.date)
    start = datetime.combine(day, hms(args.start), tzinfo=ET)
    end = datetime.combine(day, hms(args.end), tzinfo=ET)
    sim = SimClock(start)
    rec = Recorder(sim)

    import store
    import urllib.request
    pc = store.pc
    transport = store.DryRunTransport(args.date, sim.now_ns, latency_s=args.relay_latency_ms / 1000.0)
    urllib.request.urlopen = transport          # before any backend import: shim binds it as its transport
    block_network()

    import httpx
    from tape_broker import TapeAlpaca
    if not args.no_preload:
        # read every tape file BEFORE the clock starts, so file I/O and line splitting (harness work that
        # production does not have) cannot hold the GIL while the event-loop lag is being measured
        from concurrent.futures import ThreadPoolExecutor
        import store as _st
        syms = sorted({f.split(".")[0] for f in os.listdir(os.path.join(_st.base_dir(args.date), "tape"))})
        t_pre = _wall.perf_counter()
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda sk: transport.tape.rows(*sk), [(s_, k_) for s_ in syms for k_ in ("trades", "quotes")]))
        print(f"preloaded {len(syms)} symbols in {_wall.perf_counter() - t_pre:.0f}s", flush=True)
    broker_fake = TapeAlpaca(sim, transport.tape, START_EQUITY, sim.now_ns())
    if args.resume:
        broker_fake.load(os.path.join(args.resume, "broker.json"))

    from backend.app.core import broker as broker_mod
    from backend.app import main as r
    from backend.app.core import orb_integration as oi
    from backend.app.core.orb_execution import RequestBudget
    from backend.app.strategies.orbs import shim, flow
    from backend.app.models.events import TradeEvent

    real_broker_cls = broker_mod.AlpacaBroker

    def fake_broker_factory(api_key, secret_key, base_url=broker_mod.PAPER_BASE_URL, **kw):
        b = real_broker_cls(api_key, secret_key, base_url=base_url,
                            transport=httpx.MockTransport(broker_fake.handler), poll_interval_sec=0.0)
        b._sleep = lambda s: None
        return b
    r.AlpacaBroker = fake_broker_factory

    def sim_now_et():
        pinned = shim._NOW_OVERRIDE.get()
        return pinned if pinned is not None else sim.now.astimezone(ET)
    shim.now_et = sim_now_et
    oi._time = _SimTimeModule(sim)
    r.flattening_engine.clock.set_simulated_time(start)
    if not args.resume:
        r.reset_runtime_state(START_EQUITY)

    # hooks recorded before ORB is built (build binds these instance attributes)
    orig_on_event, orig_on_decision, orig_loop_sync = r.orb._on_event, r.orb._on_decision, r.orb._loop_sync
    loop_calls = {"loop_sync_max_ms": 0.0, "n": 0}

    def on_event(row):
        rec.add("orb_event", row={k: (v if isinstance(v, (int, float, str, bool, type(None), list, dict)) else str(v))
                                  for k, v in row.items()})
        return orig_on_event(row)

    def on_decision(row):
        rec.add("on_decision", row=json.loads(json.dumps(row, default=str)))
        return orig_on_decision(row)

    def loop_sync():
        t0 = _wall.perf_counter()
        try:
            return orig_loop_sync()
        finally:
            ms = (_wall.perf_counter() - t0) * 1000
            loop_calls["loop_sync_max_ms"] = max(loop_calls["loop_sync_max_ms"], ms)
            loop_calls["n"] += 1
    r.orb._on_event, r.orb._on_decision, r.orb._loop_sync = on_event, on_decision, loop_sync

    budget_off = [0.0]

    def budget_sleep(s):
        budget_off[0] += s

    out = {"date": args.date, "mode": args.mode, "macro_adapter": args.macro_adapter, "start": args.start, "end": args.end, "resume": args.resume,
           "stop_at": args.stop_at, "stop_kind": args.stop_kind, "kill_on_post": args.kill_on_post,
           "snapshots": {}, "loop": {}, "errors": []}

    def dump(final=False):
        broker_fake.dump(os.path.join(args.out, "broker.json"))
        out["records"] = rec.rows
        out["transport"] = {"counts": transport.counts, "misses": transport.misses, "log": transport.log}
        out["fills"] = broker_fake.fill_log
        out["broker_writes"] = broker_fake.write_log
        out["refused_403"] = broker_fake.refused_403
        out["final"] = final
        with open(os.path.join(args.out, "result.json.tmp"), "w") as f:
            json.dump(out, f, default=str)
        os.replace(os.path.join(args.out, "result.json.tmp"), os.path.join(args.out, "result.json"))

    if args.kill_on_post:
        def kill(row):
            if row["symbol"] == args.kill_on_post:
                rec.add("killed_on_post", symbol=row["symbol"], coid=row["client_order_id"])
                out["killed_at"] = sim.now.astimezone(ET).isoformat()
                dump()
                os._exit(0)
        broker_fake.kill_on_post = kill

    async def get_json(client, path):
        try:
            resp = await client.get(path)
            return resp.json()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    async def snapshot(client, label):
        strategies = await get_json(client, "/api/strategies")
        card = next((c for c in strategies if isinstance(c, dict) and c.get("id") == "orb"), strategies) \
            if isinstance(strategies, list) else strategies
        health = await get_json(client, "/health")
        out["snapshots"][label] = {
            "sim": sim.now.astimezone(ET).isoformat(), "orb_card": card,
            "health_orb": (health or {}).get("orb") if isinstance(health, dict) else health,
            "health_broker": (health or {}).get("broker") if isinstance(health, dict) else None,
            "api_orb": await get_json(client, "/api/orb"),
            "positions": await get_json(client, "/api/positions"),
            "account": await get_json(client, "/api/account"),
            "risk": risk_view(),
        }

    def risk_view():
        re_ = r.risk_engine
        orb_unreal = sum(p.unrealized_pnl for p in r.account.positions.values() if getattr(p, "strategy_id", "") == "orb")
        return {"equity": r.account.equity, "cash": r.account.cash, "realized": r.account.realized_pnl,
                "unrealized": r.account.unrealized_pnl, "orb_unrealized": orb_unreal,
                "starting_equity": re_.config.starting_equity,
                "hard_max_daily_loss": re_.config.hard_max_daily_loss_dollars,
                "drawdown": re_.current_drawdown_dollars, "status": str(re_.status),
                "orb_open_risk": r.orb.open_risk(),
                "positions": {s: {"qty": p.shares, "side": str(p.side), "strategy": p.strategy_id,
                                  "avg": p.avg_entry_price, "mark": p.market_price, "unreal": p.unrealized_pnl}
                              for s, p in r.account.positions.items()}}

    def wait_jobs():
        for _ in range(10000):
            futs = []
            s = r.orb.scheduler
            if s is not None:
                futs += s.running_futures()
            with flow.ABSORPTION._lock:
                futs += [f for (f, _a, _d) in flow.ABSORPTION._inflight.values()]
            pending = [f for f in futs if not f.done()]
            if not pending:
                return
            concurrent.futures.wait(pending, timeout=900)

    async def run():
        lag = {"max_ms": 0.0, "over_200": 0, "samples": 0, "worst_at": None}
        running = [True]

        async def watchdog():
            while running[0]:
                t0 = _wall.perf_counter()
                await asyncio.sleep(0.01)
                ms = (_wall.perf_counter() - t0 - 0.01) * 1000
                lag["samples"] += 1
                if ms > lag["max_ms"]:
                    lag["max_ms"], lag["worst_at"] = ms, sim.now.astimezone(ET).isoformat()
                if ms > 200:
                    lag["over_200"] += 1
                    sched = r.orb.scheduler
                    if len(lag.setdefault("events", [])) < 200:
                        lag["events"].append({"sim": sim.now.astimezone(ET).isoformat(), "ms": round(ms, 1),
                                              "jobs": sorted(sched._jobs) if sched is not None else []})

        steps_ms = {"clock_step_max_ms": 0.0, "clock_step_worst_at": None, "reconcile_max_ms": 0.0,
                    "trade_event_max_ms": 0.0, "clock_steps": 0}
        async with r.lifespan(r.app):
            # the production clock/reconcile loops read the wall clock: this harness drives their bodies itself
            # (cancel ALL of them before awaiting any: awaiting one lets another run a wall-clock step)
            doomed = [tk for tk in list(r.runtime_tasks)
                      if tk.get_name() in ("TradingRuntimeClock", "BrokerReconcile", "OR15FeedIdentity")]
            out["startup_tasks"] = sorted(tk.get_name() for tk in r.runtime_tasks)
            for tk in doomed:
                tk.cancel()
            for tk in doomed:
                try:
                    await tk
                except BaseException:
                    pass
                r.runtime_tasks.discard(tk)
            out["startup"] = {"orb_mode": r.orb.mode, "configured": r.orb.configured_mode,
                              "init_error": r.orb.init_error, "broker_mode": r.broker_state.get("mode"),
                              "mismatch_at_startup": r.broker_state.get("mismatch"),
                              "restored": r.persistence_restored, "equity": r.account.equity}
            fac = r.orb.facade
            ctl0 = r.orb.controller
            budget = RequestBudget(ctl0.cfg["request_budget_per_min"], ctl0.cfg["exit_reserve_per_min"],
                                   clock=lambda: sim.mono() + budget_off[0], sleep=budget_sleep)
            r.orb.build(r.alpaca_broker, fac, r.orb.mode, clock=sim, monotonic=sim.mono,
                        sleep=broker_fake.confirm_sleep, budget=budget)
            instrument_facade(fac, rec, sim)
            if args.macro_adapter:
                # HARNESS ADAPTER for BUG 1 (see the report): OrbsFacade.macro_veto answers (vetoed, why) but the
                # controller reads (ok, why). Flip it so the rest of the live path can be exercised.
                recorded_macro = fac.macro_veto

                def adapted_macro(symbol, direction, now):
                    vetoed, why = recorded_macro(symbol, direction, now)
                    return (not vetoed), why
                fac.macro_veto = adapted_macro
            wd = asyncio.get_running_loop().create_task(watchdog())
            client = httpx.AsyncClient(transport=httpx.ASGITransport(app=r.app), base_url="http://dryrun")
            prev_steps = {}
            t = start
            last_settle = last_reconcile = None
            validated_refusal = False
            mismatch_seen = []
            key_times = {datetime.combine(day, hms(k), tzinfo=ET) for k in KEY_TIMES}
            stop_at = datetime.combine(day, hms(args.stop_at), tzinfo=ET) if args.stop_at else None
            try:
                while t <= end:
                    sim.set(t)
                    await asyncio.to_thread(broker_fake.advance, sim.now_ns())
                    r.flattening_engine.clock.set_simulated_time(t)
                    # ADT's SIP trade handler sees the latest print of each ORB-held symbol (marks)
                    for sym, p in list(r.account.positions.items()):
                        if getattr(p, "strategy_id", "") != "orb" or not (dtime(9, 30) <= t.time() <= dtime(11, 5)):
                            continue
                        row = transport.tape.last_trade(sym, sim.now_ns())
                        if row:
                            t0 = _wall.perf_counter()
                            await r.handle_trade_event(TradeEvent(
                                symbol=sym, trade_id=int(row.get("i") or 0), price=float(row["p"]),
                                size=int(row.get("s") or 0), exchange=str(row.get("x") or ""),
                                timestamp=datetime.fromtimestamp(pc.ts_ns(row["t"]) / 1e9, timezone.utc),
                                timestamp_ns=pc.ts_ns(row["t"]), conditions=list(row.get("c") or [])))
                            steps_ms["trade_event_max_ms"] = max(steps_ms["trade_event_max_ms"],
                                                                 (_wall.perf_counter() - t0) * 1000)
                    t0 = _wall.perf_counter()
                    await r._runtime_clock_step(t)
                    ms = (_wall.perf_counter() - t0) * 1000
                    steps_ms["clock_steps"] += 1
                    if ms > steps_ms["clock_step_max_ms"]:
                        steps_ms["clock_step_max_ms"], steps_ms["clock_step_worst_at"] = ms, t.isoformat()
                    await asyncio.to_thread(wait_jobs)
                    # production _broker_reconcile_loop body on the simulated clock
                    if last_settle is None or (t - last_settle).total_seconds() >= 5:
                        last_settle = t
                        if any(o.broker_order_id or o.broker_client_id for o in r.engine.orders.values()):
                            r._settle_broker_orders()
                    if last_reconcile is None or (t - last_reconcile).total_seconds() >= args.reconcile_every:
                        last_reconcile = t
                        t0 = _wall.perf_counter()
                        await r._broker_reconcile_once()
                        steps_ms["reconcile_max_ms"] = max(steps_ms["reconcile_max_ms"], (_wall.perf_counter() - t0) * 1000)
                        if r.broker_state.get("mismatch"):
                            mismatch_seen.append({"sim": t.isoformat(), "detail": r.broker_state.get("mismatch_detail")})
                    # scheduler step transitions
                    sched = r.orb.scheduler
                    if sched is not None:
                        cur = {n: (s.get("state"), s.get("detail")) for n, s in sched.state["steps"].items()}
                        for n, v in cur.items():
                            if prev_steps.get(n) != v:
                                rec.add("step", name=n, state=v[0], detail=v[1])
                        prev_steps = cur
                    # other ADT arms must refuse a symbol ORB holds (checked once, the first time ORB holds one)
                    if not validated_refusal:
                        held = [s for s, p in r.account.positions.items() if getattr(p, "strategy_id", "") == "orb"]
                        if held:
                            validated_refusal = True
                            sym = held[0]
                            for strat, side in (("vwap_pullback", "BUY"), ("mean_reversion", "SELL"), ("news_momentum", "BUY")):
                                o = SimpleNamespace(symbol=sym, strategy_id=strat, arm=r.TradingArm.INTRADAY,
                                                    side=r.OrderSide.BUY if side == "BUY" else r.OrderSide.SELL,
                                                    qty=10, id=None, order_type=r.OrderType.MARKET, limit_price=None,
                                                    stop_price=None, estimated_price=None)
                                try:
                                    ok, why = r.pre_trade_risk_validator(o, r.account)
                                except Exception as exc:
                                    ok, why = None, f"validator raised {type(exc).__name__}: {exc}"
                                rec.add("arm_refusal_check", symbol=sym, strategy=strat, side=side, allowed=ok, why=why)
                            out["risk_while_holding"] = risk_view()
                    if t in key_times:
                        await snapshot(client, t.strftime("%H:%M"))
                    if stop_at is not None and t >= stop_at:
                        rec.add("stop", stop_kind=args.stop_kind)
                        if args.stop_kind == "hard":
                            out["loop"] = {"lag": lag, "steps": steps_ms, "loop_sync": loop_calls}
                            out["mismatch_seen"] = mismatch_seen
                            dump()
                            os._exit(0)
                        break
                    # step size: 5 s before the open, 1 s 09:30-11:05, 10 s after
                    tt = t.time()
                    step = 5 if tt < dtime(9, 30) else 1 if tt < dtime(11, 5) else 10
                    t = t + timedelta(seconds=step)
                # end of the simulated day (or graceful stop): durable trades + ledger view
                r._checkpoint_runtime("DRY_RUN_END")
                out["trades_api"] = await get_json(client, "/api/trades?range=all&limit=100")
                out["decisions_log"] = r.decision_log.recent(1000, "orb")
                out["research_setups"] = [x for x in r.research_recorder.recent.get("setups", []) if x.get("strategy_id") == "orb"]
                out["research_signals"] = [x for x in r.research_recorder.recent.get("signals", []) if x.get("strategy_id") == "orb"]
                out["pending_trades"] = list(r.pending_trade_records.values())
                out["orb_status_end"] = json.loads(json.dumps(r.orb.status(), default=str))
                out["controller_state_end"] = json.loads(json.dumps(r.orb.controller.to_state(), default=str))
                out["scheduler_state_end"] = json.loads(json.dumps(r.orb.scheduler.to_state(), default=str))
                out["account_end"] = risk_view()
                out["orb_errors"] = list(r.orb.errors)
                out["orb_alerts"] = dict(r.orb.alerts)
                out["broker_state_end"] = {k: v for k, v in r.broker_state.items() if k != "status"}
                out["mismatch_seen"] = mismatch_seen
                out["budget"] = {"used": budget.used, "throttled": budget.throttled, "borrowed": budget.borrowed}
            finally:
                running[0] = False
                await wd
                await client.aclose()
                out["loop"] = {"lag": lag, "steps": steps_ms, "loop_sync": loop_calls}
        # after the lifespan shutdown (drain + final checkpoint)
        out["after_shutdown"] = {"equity": r.account.equity, "positions": {s: p.shares for s, p in r.account.positions.items()},
                                 "realized": r.account.realized_pnl}

    try:
        asyncio.run(run())
    except Exception as exc:
        out["errors"].append(traceback.format_exc())
        print("RUN FAILED:", exc, file=sys.stderr)
    dump(final=not out["errors"])
    print(json.dumps({"date": args.date, "mode": args.mode, "errors": len(out["errors"]),
                      "misses": len(transport.misses), "fills": len(broker_fake.fill_log),
                      "writes": len(broker_fake.write_log)}))
    os._exit(1 if out["errors"] else 0)


if __name__ == "__main__":
    main()
