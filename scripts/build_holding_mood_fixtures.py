#!/usr/bin/env python3
"""DEV ONLY: write the websocket frames scripts/verify_holding_mood.py replays (docs/holding_mood/fixtures/).

The frames are built from the REAL backend serializers, not hand-typed: market_context comes from
main._market_context(), positions from main._serialize_position() over real brackets, and each adaptive
trade's entry_context from main._build_entry_context() with the engine set to the mood the trade was
admitted in (then the engine is moved to "now", so the card can say the market changed since). Only the
ORB and Tesla holdings are shaped by hand, with exactly the keys tests/test_holding_orb_context.py and the
tri controller produce (their controllers need a broker fake to run).

No network, no broker, throwaway state directory. Nothing here is imported by production code.

    python3 scripts/build_holding_mood_fixtures.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
for k in list(os.environ):
    if k.startswith(("ALPACA_", "APCA_")) or k == "RELAY_TOKEN":
        del os.environ[k]
_TMP = tempfile.mkdtemp(prefix="holding_mood_fixtures_")
os.environ.update(BROKER_MODE="local", ORB_MODE="off", START_RELAY_CLIENTS="false", ENV="development",
                  PERSISTENCE_ENABLED="false", PERSISTENCE_REQUIRED="false", RESEARCH_ENABLED="false",
                  STATE_DB_PATH=os.path.join(_TMP, "state.sqlite3"), ORB_STATE_DIR=os.path.join(_TMP, "orbs"))

import httpx  # noqa: E402


def _refuse(self, request):
    raise RuntimeError(f"fixture builder: real network request refused: {request.method} {request.url}")


httpx.HTTPTransport.handle_request = _refuse

from backend.app import main as r  # noqa: E402
from backend.app.core.account import Position, PositionSide  # noqa: E402
from backend.app.core.bracket import BracketStatus  # noqa: E402
from backend.app.core.market_filter import MarketTrend  # noqa: E402
from backend.app.models.events import OrderSide, OrderType  # noqa: E402
from backend.app.strategies import tri_engine  # noqa: E402
from backend.app.strategies.base import SignalEvent  # noqa: E402
import verify_ui_redesign as base  # noqa: E402

OUT = ROOT / "docs/holding_mood/fixtures"
T0 = datetime(2026, 9, 29, 14, 12, tzinfo=timezone.utc)      # 10:12 ET
NOW = datetime(2026, 9, 29, 15, 20, tzinfo=timezone.utc)     # 11:20 ET

VIX = {"calm": 13.0, "normal": 18.0, "nervous": 27.0, "panic": 38.0}
TREND = {"rising": MarketTrend.BULLISH, "flat": MarketTrend.NEUTRAL, "falling": MarketTrend.BEARISH,
         "unknown": MarketTrend.UNKNOWN}
ENGINE_KEYS = ("current_vix", "current_vix_regime", "current_sizing_multiplier", "current_stop_multiplier",
               "current_time_phase", "market_filter")


class Filter:
    """A market filter with a chosen trend and admission answer (a dev stand-in for SPY/QQQ bars)."""
    def __init__(self, trend: MarketTrend, reason: str = "APPROVED: aligned with the market"):
        self.trend, self.reason = trend, reason

    def get_current_trend(self, asof=None):
        return self.trend, "OK"

    def is_signal_permitted(self, **_kw):
        return True, self.reason


def set_engine(vix: Optional[float], trend: Optional[str], phase: str, *, reason: str = "APPROVED: aligned") -> None:
    eng = r.adaptation_engine
    if vix is None:
        eng.current_vix = float("nan")
    else:
        eng.on_vix_print(SimpleNamespace(value=vix, received_at=NOW))
    eng.current_time_phase = phase
    eng.market_filter = None if trend is None else Filter(TREND[trend], reason)


def fresh() -> None:
    r.reset_runtime_state(50000.0)
    r.last_vix_print = None
    for s in r.strategies:
        s.status = type(s.status).ACTIVE


def frame(*, market_status: str, trading_day: bool, positions: List[dict], market: Dict[str, Any], stamp: str) -> dict:
    p = base.base_payload(market_status=market_status, trading_day=trading_day)
    p["market_context"] = r._sanitize_for_json(market)
    p["timestamp"] = stamp
    p["all_positions"] = positions
    p["primary_position"] = positions[0] if positions else None
    p["positions_count"] = len(positions)
    if positions:
        p["account"].update(equity=50245.10, daily_pnl=245.10, daily_pnl_pct=0.49)
    return p


def market_now(vix, trend, phase, status_text=None, stale=False) -> Dict[str, Any]:
    fresh_vix = vix
    set_engine(fresh_vix, trend, phase)
    if stale:
        r.last_vix_print = SimpleNamespace(is_stale=True, is_fallback=False, asof=NOW - timedelta(minutes=7), value=vix)
        r.adaptation_engine.apply_stale_vix_guard()
    else:
        r.last_vix_print = None if vix is None else SimpleNamespace(is_stale=False, is_fallback=False, asof=NOW, value=vix)
    # asof is a fixed fake time, the live clock is not: pin the reading's age so the fixture is stable
    if r.last_vix_print is not None:
        r.last_vix_print.asof = datetime.now(timezone.utc) - timedelta(seconds=420 if stale else 3)
    ctx = r._market_context()
    if ctx.get("vix_age_seconds") is not None:
        ctx["vix_age_seconds"] = 420.0 if stale else 3.0
    return ctx


def signal(sym, side, entry, stop, strategy, features=None) -> SignalEvent:
    d = 1 if side == OrderSide.BUY else -1
    s = SignalEvent(symbol=sym, side=side, order_type=OrderType.MARKET, entry_price=entry, stop_loss=stop,
                    take_profit_1=entry + d, take_profit_2=entry + 2 * d, strategy_id=strategy, confidence=0.9,
                    reason="FIXTURE", timestamp=T0, stop_is_final=strategy == "vwap_pullback")
    s.features = features or {}
    return s


def adaptive_holding(sym: str, strategy: str, side: OrderSide, entry: float, raw_stop: float, market_price: float,
                     *, entry_mood: tuple, now_mood: tuple, stages: Optional[dict] = None, features: Optional[dict] = None,
                     policy: str = "TARGET", t1_done: bool = False, trail_to: Optional[float] = None,
                     with_context: bool = True) -> dict:
    """One real bracket + position, its entry_context built while the engine is in the entry mood."""
    vix, trend, phase, reason = entry_mood
    set_engine(vix, trend, phase, reason=reason)
    r.last_vix_print = None
    sig = signal(sym, side, entry, raw_stop, strategy, features)
    stop = r.adaptation_engine.calculate_adapted_stop(sig)
    qty = r.adaptation_engine.calculate_adapted_size(r.account.equity, entry, stop)
    ctx = r._build_entry_context(sig, {"admission_qty": qty, **(stages or {})}, qty, stop) if with_context else None
    long_ = side == OrderSide.BUY
    r.account.positions[sym] = Position(symbol=sym, side=PositionSide.LONG if long_ else PositionSide.SHORT,
                                        shares=qty, avg_entry_price=entry, market_price=market_price,
                                        strategy_id=strategy)
    b = r.bracket_manager.create_bracket(f"brk_{sym}", sym, "LONG" if long_ else "SHORT", qty, entry, stop,
                                         strategy_id=strategy, runner_policy=policy, timestamp=T0)
    b.status = BracketStatus.ACTIVE
    b.entry_context = ctx
    if t1_done:
        b.status, b.target_1_filled = BracketStatus.TARGET_1_HIT, True
        b.remaining_qty = qty - qty // 2
        b.current_stop_price = trail_to if trail_to is not None else entry
    now_vix, now_trend, now_phase = now_mood
    market_now(now_vix, now_trend, now_phase)
    return r._serialize_position(sym, include_chart=False)


def orb_holding(sym="PLTR") -> dict:
    fresh_pos = Position(symbol=sym, side=PositionSide.LONG, shares=180, avg_entry_price=41.20, market_price=41.85,
                         strategy_id="orb")
    r.account.positions[sym] = fresh_pos
    out = r._serialize_position(sym, include_chart=False)     # bracketless -> falls back to its own id "orb"
    out.update(
        strategy_id="orb", fixed_protection=True, take_profit_2=None, stop_loss=40.20, take_profit_1=42.35,
        r_multiple=0.65, exit_due=datetime(2026, 9, 29, 11, 0, tzinfo=timezone(timedelta(hours=-4))).isoformat(),
        orb_context={"classification": "CALM_TREND", "short_frac": 0.4, "wave": "primary",
                     "decided_at": "2026-09-29T09:38:04-04:00", "short_bounds": {"min": 0.25, "max": 0.75},
                     "flow_rules_on": ["candle", "delta", "velocity", "macro", "absorption"], "breakeven_r": 0.75,
                     "risk_usd": 1000.0, "flatten_at": "11:00"})
    return out


def tesla_holding() -> dict:
    r.account.positions["TSLA"] = Position(symbol="TSLA", side=PositionSide.LONG, shares=20, avg_entry_price=412.0,
                                           market_price=415.5, strategy_id="tsla_asymmetric_dual")
    out = r._serialize_position("TSLA", include_chart=False)
    tr = lambda i, target, due: {"id": i, "qty": 10, "closed_qty": 0, "target": target, "target_r": 1.0 + i,
                                 "stop": 405.0, "exit_due": due, "exit_reason": None,
                                 "protection_confirmed": True, "protection_terminal": False}
    tranches = [tr(1, 419.0, "2026-09-29T11:12:00-04:00"), tr(2, 426.0, "2026-09-29T11:12:00-04:00")]
    out.update(strategy_id="tsla_asymmetric_dual", fixed_protection=True, stop_loss=405.0, take_profit_1=419.0,
               take_profit_2=426.0, exit_due="2026-09-29T11:12:00-04:00", tranches=tranches,
               plan_risk_pct=round(tri_engine.TRI_RISK_PCT * 100.0, 4))
    return out


def unlinked_holding() -> dict:
    r.account.positions["AMD"] = Position(symbol="AMD", side=PositionSide.LONG, shares=15, avg_entry_price=190.0,
                                          market_price=191.2, strategy_id="MANUAL")
    return r._serialize_position("AMD", include_chart=False)


def write(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(json.dumps(data, allow_nan=False))       # a fixture with a NaN would be a fixture bug
    (OUT / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")


def main() -> int:
    stamp = "2026-09-29T15:20:00Z"
    names: List[str] = []

    def emit(name, **kw):
        write(name, frame(stamp=stamp, **kw))
        names.append(name)

    # idle: market closed, nothing held (a Sunday-night style page)
    fresh()
    emit("idle", market_status="CLOSED", trading_day=False, positions=[],
         market=market_now(18.0, "unknown", "POST_MARKET"))
    emit("weekend", market_status="CLOSED", trading_day=False, positions=[],
         market=market_now(None, None, "POST_MARKET"))
    # waiting for the first market data while the market is open
    emit("waiting", market_status="OPEN", trading_day=True, positions=[], market=market_now(None, None, "TREND_CONTINUATION"))
    # the 4 fear levels x 4 market directions, morning
    for level, v in VIX.items():
        for direction in TREND:
            emit(f"mood_{level}_{direction}", market_status="OPEN", trading_day=True, positions=[],
                 market=market_now(v, direction, "TREND_CONTINUATION"))
    emit("mood_nervous_rising_midday", market_status="OPEN", trading_day=True, positions=[],
         market=market_now(27.0, "rising", "MIDDAY_CHOP"))
    emit("stale_vix", market_status="OPEN", trading_day=True, positions=[],
         market=market_now(13.0, "rising", "TREND_CONTINUATION", stale=True))

    # busy: Ride the Trend past target 1 (trailed), Big News extreme catalyst, Snap Back cap-bound, ORB, Tesla
    fresh()
    nervous_mid = (27.0, "rising", "MIDDAY_CHOP", "APPROVED: aligned")
    normal_rising = (18.0, "rising", "TREND_CONTINUATION")
    rtt = adaptive_holding(
        "AAPL", "vwap_pullback", OrderSide.BUY, 236.10, 234.80, 238.90,
        entry_mood=(27.0, "rising", "TREND_CONTINUATION", "APPROVED: aligned"), now_mood=normal_rising,
        stages={"rs_day": 0.004, "rs_30": 0.002, "macro": "MACRO_CLEAR", "regime_enforced": []},
        features={"stop_atr_candidate": 1.30, "stop_structure_candidate": 0.90},
        policy="TRAIL_ONLY", t1_done=True, trail_to=236.40)
    news = adaptive_holding(
        "NVDA", "news_momentum", OrderSide.BUY, 184.00, 179.40, 185.10,
        entry_mood=(27.0, "unknown", "TREND_CONTINUATION", "APPROVED_EXTREME_CATALYST: |S|=0.91 vol=6.2x"),
        now_mood=normal_rising)
    snap = adaptive_holding(
        "MSFT", "mean_reversion", OrderSide.SELL, 512.00, 512.60, 511.20,
        entry_mood=(18.0, "flat", "TREND_CONTINUATION", "APPROVED: Mean reversion permitted in NEUTRAL market"),
        now_mood=normal_rising)
    busy_positions = [rtt, news, snap, orb_holding(), tesla_holding()]
    emit("busy", market_status="OPEN", trading_day=True, positions=busy_positions,
         market=market_now(18.0, "rising", "TREND_CONTINUATION"))

    # trades that were sized DOWN: jumpy market alone, jumpy + midday, and one the account's limits trimmed
    fresh()
    jumpy = adaptive_holding(
        "NVDA", "news_momentum", OrderSide.BUY, 184.00, 179.40, 185.10,
        entry_mood=(27.0, "rising", "TREND_CONTINUATION", "APPROVED: aligned"), now_mood=normal_rising)
    midday = adaptive_holding(
        "GOOGL", "mean_reversion", OrderSide.SELL, 250.00, 253.50, 249.10,
        entry_mood=(27.0, "flat", "MIDDAY_CHOP", "APPROVED: Mean reversion permitted in NEUTRAL market"),
        now_mood=(27.0, "rising", "MIDDAY_CHOP"))
    trimmed = adaptive_holding(
        "AMZN", "news_momentum", OrderSide.BUY, 210.00, 204.90, 210.50,
        entry_mood=(38.0, "rising", "TREND_CONTINUATION", "APPROVED: aligned"),
        now_mood=(27.0, "rising", "TREND_CONTINUATION"), stages={"admission_qty": 400})
    emit("sized_down", market_status="OPEN", trading_day=True, positions=[jumpy, midday, trimmed],
         market=market_now(27.0, "rising", "TREND_CONTINUATION"))

    # a trade bought before the robot kept this record, and one the robot did not place
    fresh()
    pre = adaptive_holding("AAPL", "vwap_pullback", OrderSide.BUY, 236.10, 234.80, 238.90,
                           entry_mood=normal_rising + ("APPROVED: aligned",), now_mood=normal_rising,
                           features={"stop_atr_candidate": 1.30, "stop_structure_candidate": 0.90},
                           policy="TRAIL_ONLY", with_context=False)
    emit("pre_release_and_unlinked", market_status="OPEN", trading_day=True, positions=[pre, unlinked_holding()],
         market=market_now(18.0, "rising", "TREND_CONTINUATION"))

    # an old backend during a deploy: none of the new keys, positions without the new fields
    old_market = {k: v for k, v in market_now(18.0, "rising", "TREND_CONTINUATION").items()
                  if k in ("vix", "vix_regime", "time_phase", "market_status", "sizing_multiplier", "stop_multiplier")}
    old_positions = []
    for pos in (pre,):
        old = {k: v for k, v in pos.items() if k not in (
            "entry_context", "initial_stop", "bracket_status", "runner_policy", "target_1_filled", "orb_context",
            "plan_risk_pct", "r_multiple")}
        old["take_profit_2"] = pos["take_profit_2"]
        old_positions.append(old)
    emit("old_backend", market_status="OPEN", trading_day=True, positions=old_positions, market=old_market)

    (OUT / "index.json").write_text(json.dumps({"states": names, "built_from": "backend.app.main serializers"}, indent=1) + "\n")
    print(f"wrote {len(names)} frames to {OUT}")
    import shutil
    shutil.rmtree(_TMP, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
