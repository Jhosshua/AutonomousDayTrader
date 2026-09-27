"""Full dry run of Ride the Trend v2 on a REAL session: 1-minute bars plus every SIP trade
print and NBBO quote of the morning, replayed through main.handle_bar_event /
handle_trade_event / handle_quote_event in simulation mode (no broker, no network).

Data: research/vwap_trend_2026_09_27/data/bars/<SYM>.jsonl (full days) and
      research/vwap_trend_2026_09_27/data/ticks/<DATE>/<SYM>.{trades,quotes}.jsonl
      (fetch_bars.py / fetch_ticks.py, gitignored).

Usage: python3 scripts/run_ride_the_trend_v2_dry_run.py 2026-09-25 [--all-arms] [--symbols AAPL,NVDA]
Writes docs/ride_the_trend_v2/dry_run_<DATE>[_all_arms].json and .md. Exit 1 on any invariant failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

from backend.app import main as r  # noqa: E402
from backend.app.models.events import BarEvent, QuoteEvent, TradeEvent, parse_relay_timestamp  # noqa: E402
from backend.app.strategies.vwap_pullback_v2 import WINDOW_CLOSE, WINDOW_OPEN  # noqa: E402

ET = ZoneInfo("America/New_York")
DATA = ROOT / "research" / "vwap_trend_2026_09_27" / "data"
OUT = ROOT / "docs" / "ride_the_trend_v2"
V2_SYMBOLS = ["AAPL", "NVDA", "AMD", "MSFT", "AMZN", "META", "GOOGL", "PLTR", "COIN"]
INDEX = ["SPY", "QQQ"]
REGIME = ["XLK", "XLC", "XLY", "XLF", "UUP", "SHY", "IEF"]


@contextmanager
def no_network():
    def forbidden(*_a, **_k):
        raise AssertionError("dry run attempted network access")
    with patch("socket.socket.connect", forbidden), patch("socket.create_connection", forbidden):
        yield


def load_prior_bars(sym: str, day: str, sessions: int = 5) -> List[BarEvent]:
    """Bars of the `sessions` regular sessions strictly before `day` (for the volume profile)."""
    days: Dict[str, List[BarEvent]] = {}
    with open(DATA / "bars" / f"{sym}.jsonl") as f:
        for line in f:
            b = json.loads(line)
            ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            et = ts.astimezone(ET)
            d = et.date().isoformat()
            if d >= day or et.time() < datetime.min.time().replace(hour=9, minute=30) or et.time() >= datetime.min.time().replace(hour=16):
                continue
            days.setdefault(d, []).append(BarEvent(symbol=sym, open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=int(b["v"]), timestamp=ts))
    out = []
    for d in sorted(days)[-sessions:]:
        out.extend(days[d])
    return out


def load_bars(sym: str, day: str) -> List[BarEvent]:
    out = []
    with open(DATA / "bars" / f"{sym}.jsonl") as f:
        for line in f:
            if day not in line[:40]:
                continue
            b = json.loads(line)
            ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            et = ts.astimezone(ET).time()
            if et < datetime.min.time().replace(hour=9, minute=30) or et >= datetime.min.time().replace(hour=16):
                continue
            out.append(BarEvent(symbol=sym, open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=int(b["v"]), timestamp=ts))
    return out


def load_ticks(sym: str, day: str) -> Tuple[List[TradeEvent], List[QuoteEvent]]:
    d = DATA / "ticks" / day
    trades, quotes = [], []
    tp, qp = d / f"{sym}.trades.jsonl", d / f"{sym}.quotes.jsonl"
    if tp.exists():
        with open(tp) as f:
            for line in f:
                row = json.loads(line)
                row["S"] = sym
                trades.append(TradeEvent.from_relay_dict(row))
    if qp.exists():
        with open(qp) as f:
            for line in f:
                row = json.loads(line)
                row["S"] = sym
                quotes.append(QuoteEvent.from_relay_dict(row))
    return trades, quotes


async def run(day: str, all_arms: bool, symbols: List[str]) -> Dict[str, Any]:
    r.reset_runtime_state()
    r.set_simulation_mode(True)
    assert r.engine.broker is None and r.state_store is None
    r.relay_statuses["stock"] = "connected"
    for s in r.strategies:
        if all_arms or s.strategy_id == "vwap_pullback":
            s.resume()
        else:
            s.pause()
    r.vwap_strategy.reset_daily_stats()
    # Add-on C: profiles from the five sessions before the replay day, exactly as the live rebuild would
    r.profile_store.profiles.clear(); r.profile_store.errors.clear()
    for sym in symbols:
        r.profile_store.build(sym, load_prior_bars(sym, day), day)
    setup_events: List[Dict[str, Any]] = []
    old_sink = r.vwap_pullback_v2.EVENT_SINK

    def sink(ev):
        setup_events.append({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in ev.items()})
        old_sink(ev)
    r.vwap_pullback_v2.EVENT_SINK = sink

    events: List[Tuple[int, int, Any]] = []  # (time_ns, priority, event); bars arrive at bar end
    bar_syms = list(dict.fromkeys(INDEX + REGIME + symbols + (["TSLA"] if all_arms else [])))
    stock_bars: Dict[str, List[BarEvent]] = {}
    for sym in bar_syms:
        bars = load_bars(sym, day)
        stock_bars[sym] = bars
        for b in bars:
            events.append((int(b.timestamp.timestamp() * 1e9) + 60_000_000_000, 2 if sym in INDEX + REGIME else 3, b))
    n_tr = n_q = 0
    for sym in symbols:
        trades, quotes = load_ticks(sym, day)
        n_tr += len(trades); n_q += len(quotes)
        events.extend((t.timestamp_ns, 1, t) for t in trades)
        events.extend((q.timestamp_ns, 0, q) for q in quotes)
    events.sort(key=lambda e: (e[0], e[1]))
    t0 = time.time()
    from backend.app.core.tick_tape import TickTape
    audit_tape = TickTape(keep_seconds=8 * 3600)   # diagnostics only: keeps the whole morning
    try:
        with no_network():
            for _, _, ev in events:
                if isinstance(ev, BarEvent):
                    await r.handle_bar_event(ev)
                elif isinstance(ev, TradeEvent):
                    audit_tape.on_trade(ev.symbol, ev.price, ev.size, ev.timestamp_ns, trade_id=ev.trade_id,
                                        exchange=ev.exchange, conditions=ev.conditions)
                    await r.handle_trade_event(ev)
                else:
                    audit_tape.on_quote(ev.symbol, ev.bid_price, ev.ask_price, ev.bid_size, ev.ask_size, ev.timestamp_ns)
                    await r.handle_quote_event(ev)
            close = datetime.fromisoformat(f"{day}T16:05:00").replace(tzinfo=ET)
            r.flattening_engine.clock.set_simulated_time(close)
            directive = r.flattening_engine.check_time_tick()
            if directive:
                await r.handle_flattening_directive(directive)
    finally:
        r.vwap_pullback_v2.EVENT_SINK = old_sink
    elapsed = time.time() - t0

    # ---- collect ----------------------------------------------------------------
    decisions = [d for d in r.decision_log.recent(300, "vwap_pullback")]
    signals = [row for row in r.research_recorder.recent["signals"] if row.get("strategy_id") == "vwap_pullback"]
    trades_v2 = [t for t in r.pending_trade_records.values() if t.get("strategy_id") == "vwap_pullback"]
    closed = [t for t in getattr(r, "completed_trade_records", []) if t.get("strategy_id") == "vwap_pullback"] if hasattr(r, "completed_trade_records") else []
    brackets = [b for b in r.bracket_manager.brackets.values() if b.strategy_id == "vwap_pullback"]
    orders = [o for o in r.engine.orders.values() if getattr(o, "strategy_id", "") == "vwap_pullback"]
    card = r.vwap_strategy.to_dict()
    funnel = Counter(e["event"] for e in setup_events)

    # ---- invariants -------------------------------------------------------------
    failures: List[str] = []
    for o in orders:
        if getattr(o, "bracket_role", None) is None and str(getattr(o.order_type, "value", o.order_type)).upper() != "MARKET":
            failures.append(f"entry order {o.id} is not MARKET")
    for b in brackets:
        if b.runner_policy != "TRAIL_ONLY" or b.target_2_order_id is not None:
            failures.append(f"bracket {b.bracket_id} runner policy {b.runner_policy} t2={b.target_2_order_id}")
        dist = abs(b.entry_price - b.initial_stop_price) / b.entry_price
        if not (0.0039 <= dist <= 0.0401):
            failures.append(f"bracket {b.bracket_id} stop distance {dist:.4%} outside [0.4%, 4%]")
    for row in signals:
        ts = datetime.fromisoformat(row["signal"]["timestamp"]).astimezone(ET).time()
        if not (WINDOW_OPEN <= ts < WINDOW_CLOSE):
            failures.append(f"signal at {ts} outside the window")
        f = row["signal"].get("features") or {}
        for key in ("layer1_pullback_delta_ratio", "layer2_book_imbalance", "layer3_tick_velocity_atr_per_min"):
            if f.get(key) is None:
                failures.append(f"signal {row['row_id']} missing {key}")
        if row["symbol"] in ("TSLA", "CDE", "SPY", "QQQ"):
            failures.append(f"signal on excluded symbol {row['symbol']}")
    if any(e["symbol"] in ("TSLA", "CDE", "SPY", "QQQ") for e in setup_events):
        failures.append("setup events on excluded symbols")
    if n_tr == 0 or n_q == 0:
        failures.append("no ticks were replayed")
    if r.regime_feed.bars_seen == 0:
        failures.append("no regime ETF bars were replayed")
    n_bars = sum(1 for e in events if isinstance(e[2], BarEvent))
    if n_bars < 300 * len(symbols):
        failures.append(f"only {n_bars} bars were replayed")
    if funnel.get("IMPULSE", 0) == 0:
        failures.append("no setups formed: the evaluator never ran")
    if all_arms is False and setup_events and not any(e["event"] in ("PULLBACK", "PVR_NOT_THIN", "HIGH_VOLUME_PULLBACK", "TICK_UNAVAILABLE", "AGGRESSIVE_PULLBACK", "NO_TOUCH", "TOUCH_TOO_EARLY") for e in setup_events):
        failures.append("no impulse ever resolved: touch logic never ran")
    tape_h = r.tick_tape.health()

    # ---- funnel diagnostics (plan part 2, step 0) --------------------------------------
    measured = [e for e in setup_events if e["event"] == "RESUMPTION_MEASURED"]
    pre_empted = sum(1 for e in setup_events if e["event"] == "IMPULSE" and (e["detail"] or {}).get("pre_empted"))
    diag_rows = []
    would_fail = Counter()
    unavailable = Counter()
    for e in measured:
        d = e["detail"] or {}
        sym, side = e["symbol"], e["side"]
        sgn = 1.0 if side == "LONG" else -1.0
        feasible = None
        if all(d.get(k) is not None for k in ("ext_close", "atr", "vwap", "std", "age")):
            need = d["ext_close"] + sgn * 0.5 * d["age"] * d["atr"]      # slope requires at least this close
            cap = d["vwap"] + sgn * 0.5 * d["std"]                      # chase cap allows at most this close
            feasible = (need <= cap) if side == "LONG" else (need >= cap)
        t_end = datetime.fromisoformat(e["bar_ts"]) + timedelta(minutes=1)
        # Layer 4 evidence exactly as the evaluator recorded it at decision time (no hindsight)
        rec = d.get("regime") or {}
        reg = {"failed": rec.get("failed", []), "unavailable": rec.get("unavailable", []) if rec else ["NOT_RECORDED"],
               "measures": {"sector_rs_30m": rec.get("sector_rs_30m"),
                            "dollar_return_30m": {"return": rec.get("dollar_30m")} if rec.get("dollar_30m") is not None else None,
                            "ief_return_30m": {"return": rec.get("ief_30m")} if rec.get("ief_30m") is not None else None}}
        row = {"symbol": sym, "side": side, "time_et": t_end.astimezone(ET).strftime("%H:%M"), "age": d.get("age"),
               "slope": d.get("slope"), "chase_std": d.get("chase_std"), "feasible_price_interval": feasible,
               "impulse_delta": d.get("impulse_delta"), "pullback_delta": d.get("pullback_delta"),
               "resumption_delta": d.get("resumption_delta"), "rolling_delta": d.get("rolling_delta"),
               "tick_velocity_atr_per_min": d.get("tick_velocity_atr_per_min"), "book_imbalance": d.get("book_imbalance"),
               "session_delta": d.get("session_delta"), "spread_ratio": d.get("spread_ratio"),
               "spread_now_bps": d.get("spread_now_bps"), "hvn_support": bool(d.get("hvn_support")),
               "hvn_overhead": bool(d.get("hvn_overhead")), "profile_sessions": d.get("profile_sessions"),
               "regime_failed": reg["failed"], "regime_unavailable": reg["unavailable"],
               "sector_rs_30m": reg["measures"].get("sector_rs_30m"),
               "dollar_30m": (reg["measures"].get("dollar_return_30m") or {}).get("return"),
               "ief_30m": (reg["measures"].get("ief_return_30m") or {}).get("return")}
        diag_rows.append(row)
        for name, val, thr in (("IMPULSE_DELTA", d.get("impulse_delta"), 0.15), ("RESUMPTION_DELTA", d.get("resumption_delta"), 0.10)):
            if val is None:
                unavailable[name] += 1
            elif sgn * val < thr:
                would_fail[name] += 1
        if d.get("rolling_delta") is None:
            unavailable["ROLLING_DELTA"] += 1
        elif sgn * d["rolling_delta"] <= 0:
            would_fail["ROLLING_DELTA"] += 1
        for g in reg["failed"]:
            would_fail[g] += 1
        for g in reg["unavailable"]:
            unavailable[g] += 1
        # add-ons (enforced live; counted here per evaluation for the record)
        if d.get("session_delta") is None:
            unavailable["SESSION_DELTA"] += 1
        elif sgn * d["session_delta"] <= 0:
            would_fail["SESSION_DELTA_AGAINST"] += 1
        if d.get("spread_ratio") is None:
            unavailable["SPREAD"] += 1
        elif d["spread_ratio"] > 1.5 or (d.get("spread_now_bps") or 0) > 30:
            would_fail["SPREAD_WIDE"] += 1
        if d.get("profile_sessions") is None:
            unavailable["PROFILE"] += 1
        else:
            if not d.get("hvn_support"):
                would_fail["HVN_NO_SUPPORT"] += 1
            if d.get("hvn_overhead"):
                would_fail["HVN_OVERHEAD"] += 1
    quote_share = {}
    for sym in symbols:
        t0 = int(datetime.fromisoformat(f"{day}T09:45:00").replace(tzinfo=ET).timestamp() * 1e9)
        t1 = int(datetime.fromisoformat(f"{day}T11:30:00").replace(tzinfo=ET).timestamp() * 1e9)
        dd = audit_tape.delta(sym, t0, t1, min_classified_share=0.0, min_quote_share=0.0)
        quote_share[sym] = {"quote": round(dd["quote_share"], 3), "classified": round(dd["classified_share"], 3),
                            "complete": dd["complete"]} if dd else None
    diagnostics = {
        "resumption_evaluations": len(measured),
        "impulses_pre_empting_a_setup": pre_empted,
        "feasible_price_interval": Counter(str(r_["feasible_price_interval"]) for r_ in diag_rows),
        "would_fail_if_enforced": dict(would_fail), "unavailable": dict(unavailable),
        "quote_classified_share_0945_1130": quote_share,
        "rows": diag_rows,
    }

    report = {
        "date": day, "all_arms": all_arms, "symbols": symbols, "elapsed_s": round(elapsed, 1),
        "events_replayed": {"bars": sum(1 for e in events if isinstance(e[2], BarEvent)), "trades": n_tr, "quotes": n_q},
        "tape": {"trades_seen": tape_h["trades_seen"], "quotes_seen": tape_h["quotes_seen"]},
        "funnel": dict(sorted(funnel.items())),
        "decisions": decisions,
        "decision_summary": r.decision_log.summary("vwap_pullback"),
        "signals": [{"symbol": s["symbol"], "time_et": datetime.fromisoformat(s["signal"]["timestamp"]).astimezone(ET).strftime("%H:%M"),
                     "side": s["signal"]["side"], "entry": s["signal"]["entry_price"], "stop": s["signal"]["floored_stop"],
                     "t1": s["signal"]["target_1"], "outcome": s["outcome"], "detail": s["detail"][:120],
                     "layers": {k: (s["signal"].get("features") or {}).get(k) for k in
                                ("pvr", "slope_atr_per_bar", "layer1_pullback_delta_ratio", "layer2_book_imbalance",
                                 "layer3_tick_velocity_atr_per_min", "stop_dist_pct")}} for s in signals],
        "brackets": [{"id": b.bracket_id, "symbol": b.symbol, "side": b.side, "status": b.status.value, "entry": b.entry_price,
                      "stop0": b.initial_stop_price, "stop_now": b.current_stop_price, "t1": b.target_1_price,
                      "runner_policy": b.runner_policy, "t2_order": b.target_2_order_id, "qty": b.total_qty,
                      "remaining": b.remaining_qty} for b in brackets],
        "account": {"equity": round(r.account.equity, 2), "realized_pnl": round(r.account.realized_pnl, 2),
                    "positions_open": len(r.account.positions)},
        "trades_by_strategy": dict(Counter(t.get("strategy_id") for t in r.pending_trade_records.values())),
        "card": {k: card[k] for k in ("id", "version", "mode", "excluded_symbols", "first_possible_signal", "today_counts",
                                      "signals_emitted_today", "data_layers")},
        "failures": failures,
        "regime": r.regime_feed.health(),
        "profiles": r.profile_store.health(),
        "diagnostics": diagnostics,
        "setup_events": [e for e in setup_events if e["event"] != "FEATURE_UNAVAILABLE"],
    }
    r.reset_runtime_state()
    r.set_simulation_mode(False)
    return report


def write_md(rep: Dict[str, Any], path: Path) -> None:
    lines = [f"# Ride the Trend v2 dry run, {rep['date']}" + (" (all arms)" if rep["all_arms"] else ""), ""]
    lines.append(f"Replayed {rep['events_replayed']['bars']} bars, {rep['events_replayed']['trades']:,} trade prints and "
                 f"{rep['events_replayed']['quotes']:,} quotes for {', '.join(rep['symbols'])} in {rep['elapsed_s']} s, "
                 f"simulation mode, no broker, no network.")
    lines.append("")
    lines.append("## Funnel (every setup transition and rejection)")
    for k, v in rep["funnel"].items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append(f"## Signals ({len(rep['signals'])}) and what the bot decided")
    for s in rep["signals"]:
        lines.append(f"- {s['time_et']} {s['symbol']} {s['side']} entry {s['entry']} stop {s['stop']} T1 {s['t1']} -> {s['outcome']} ({s['detail']}); layers {s['layers']}")
    lines.append("")
    lines.append("## Brackets")
    for b in rep["brackets"]:
        lines.append(f"- {b['symbol']} {b['side']} {b['status']} qty {b['qty']} entry {b['entry']} stop {b['stop0']} -> {b['stop_now']} T1 {b['t1']} runner {b['runner_policy']} t2_order {b['t2_order']}")
    lines.append("")
    lines.append(f"Account: {rep['account']}. Trades by strategy: {rep['trades_by_strategy']}.")
    lines.append(f"Decision summary: {rep['decision_summary']}.")
    lines.append(f"Data layers on the card: {json.dumps(rep['card']['data_layers'])}")
    lines.append("")
    dg = rep["diagnostics"]
    lines.append("## Part-2 diagnostics (measures recorded on every resumption evaluation)")
    lines.append(f"- Resumption evaluations: {dg['resumption_evaluations']}; impulses that pre-empted a live setup: {dg['impulses_pre_empting_a_setup']}")
    lines.append(f"- Feasible entry price interval (slope vs chase cap): {dict(dg['feasible_price_interval'])}")
    lines.append(f"- Would fail if enforced: {dg['would_fail_if_enforced']}; unavailable: {dg['unavailable']}")
    lines.append(f"- Quote-classified share of volume 09:45-11:30: {dg['quote_classified_share_0945_1130']}")
    for row in dg["rows"]:
        lines.append(f"  - {row['time_et']} {row['symbol']} {row['side']} age {row['age']} slope {row['slope']:.2f} chase {row['chase_std']:.2f} feasible {row['feasible_price_interval']} "
                     f"imp {row['impulse_delta']} pull {row['pullback_delta']} res {row['resumption_delta']} roll {row['rolling_delta']} "
                     f"vel {row['tick_velocity_atr_per_min']} book {row['book_imbalance']} sess {row['session_delta']} spread {row['spread_ratio']} "
                     f"hvn_support {row['hvn_support']} hvn_overhead {row['hvn_overhead']} regime_failed {row['regime_failed']} unavailable {row['regime_unavailable']}")
    lines.append("")
    lines.append("## Invariants")
    lines.append("PASS: all invariants held." if not rep["failures"] else "FAIL:\n" + "\n".join(f"- {f}" for f in rep["failures"]))
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--all-arms", action="store_true")
    ap.add_argument("--symbols", default=",".join(V2_SYMBOLS))
    a = ap.parse_args()
    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    rep = asyncio.run(run(a.date, a.all_arms, symbols))
    OUT.mkdir(parents=True, exist_ok=True)
    suffix = "_all_arms" if a.all_arms else ""
    (OUT / f"dry_run_{a.date}{suffix}.json").write_text(json.dumps(rep, indent=1, default=str))
    write_md(rep, OUT / f"dry_run_{a.date}{suffix}.md")
    print(json.dumps({k: rep[k] for k in ("events_replayed", "funnel", "decision_summary", "account", "trades_by_strategy", "failures")}, indent=1))
    print("SIGNALS:", json.dumps(rep["signals"], indent=1, default=str))
    return 1 if rep["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
