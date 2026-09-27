"""backend/app/strategies/vwap_pullback_v2.py
Ride the Trend v2: VWAP-structure trend, thin-volume pullback, resumption-speed entry.

Rules: PLAN_2026_09_27_ride_the_trend_v2.md section 2. The evaluator is a deterministic
per-symbol state machine over the accepted session bars; appending later bars never
changes an earlier transition, and a restart rebuilds the machine by replaying the
session bars it already saw.

States: IDLE -> IMPULSE -> PULLBACK -> RESUMING -> SIGNAL (then IDLE).
Long side is written first; the short side mirrors with highs/lows and signs swapped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
import math
from typing import Any, Callable, Dict, List, Optional, Tuple
import zoneinfo

from backend.app.models.events import BarEvent, OrderSide, OrderType
from backend.app.strategies.base import (
    MIN_STOP_DISTANCE_PCT,
    SignalEvent,
    Strategy,
    StrategyStatus,
    attach_features,
)

ET_TZ = zoneinfo.ZoneInfo("America/New_York")

VERSION = "v2"
POLICY_ID = "V2_FULL_L2_2026_09_28"
STRATEGY_ID = "vwap_pullback"

# Session bars are only accepted inside the regular session.
SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)
# Signals are allowed only inside this window; an active setup is discarded at its end.
WINDOW_OPEN = dtime(9, 45)
WINDOW_CLOSE = dtime(11, 30)
FIRST_POSSIBLE_SIGNAL = "10:09"  # index 38 completes at 10:09 ET (30-bar reference after 5 opening bars)

MAX_STOP_DISTANCE_PCT = 0.0400

# Every event name the machine can emit (the research funnel counts these).
EVENTS = (
    "DUP_BAR", "LATE_BAR", "BAD_BAR", "FEED_GAP", "ZERO_VOLUME_BAR", "FEATURE_UNAVAILABLE",
    "AMBIGUOUS_BAR", "IMPULSE", "TOUCH_TOO_EARLY", "NO_TOUCH", "HIGH_VOLUME_PULLBACK",
    "PVR_NOT_THIN", "TICK_UNAVAILABLE", "AGGRESSIVE_PULLBACK", "PULLBACK", "PULLBACK_TIMEOUT",
    "RESUMING", "NEW_EXTREME", "RESUMPTION_TOO_OLD", "NO_UP_CLOSE", "SLOPE_TOO_SLOW", "CHASED",
    "TICK_VELOCITY_LOW", "BOOK_UNAVAILABLE", "BOOK_AGAINST", "WINDOW_CLOSED",
    "EMISSION_DISABLED", "STOP_TOO_WIDE", "SIGNAL",
    # part 2 (recorded on every candidate; gates only when enforced)
    "IMPULSE_NOT_AGGRESSIVE", "RESUMPTION_NOT_AGGRESSIVE", "CUM_DELTA_AGAINST", "RESUMPTION_MEASURED",
    # add-ons (enforced from day one): session cumulative delta, spread proxy, volume-profile nodes
    "SESSION_DELTA_AGAINST", "SPREAD_WIDE", "HVN_NO_SUPPORT", "HVN_OVERHEAD", "PROFILE_UNAVAILABLE",
)
PART2_GATES = ("IMPULSE_DELTA", "RESUMPTION_DELTA", "ROLLING_DELTA")

# Optional research sink: main.py installs a callable that receives every event dict.
# Kept at module level (not on the strategy object) so the checkpoint never sees it.
EVENT_SINK: Optional[Callable[[Dict[str, Any]], None]] = None
# The live tick tape (backend/app/core/tick_tape.TickTape), installed by main.py. Module
# level for the same reason. Layers 1-3 read from it; without it every setup fails closed.
TAPE: Optional[Any] = None
# The live regime feed (backend/app/core/regime_feed.RegimeFeed), installed by main.py, so every
# resumption evaluation records the Layer 4 evidence as of decision time.
REGIME: Optional[Any] = None
# The volume-profile store (backend/app/core/volume_profile.ProfileStore), installed by main.py.
PROFILE: Optional[Any] = None
NS = 1_000_000_000


def _safe(fn, *args, **kwargs):
    """Measurement only: a failure is recorded as unavailable, never raised into the bar loop."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _ok(d: Optional[Dict[str, Any]], key: str = "delta_ratio") -> Optional[Dict[str, Any]]:
    """A tape result usable as evidence: present, complete, finite."""
    if not d or not d.get("complete", False):
        return None
    try:
        if not math.isfinite(float(d[key])):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return d


def _ns(ts: datetime) -> int:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return int(ts.timestamp() * NS)


@dataclass
class V2Params:
    ref_bars: int = 30            # reference volume window and impulse lookback
    ref_min_start: int = 5        # first five opening bars are never in the reference
    touch_min_bars: int = 2       # touch must come at least this many bars after the impulse
    touch_max_bars: int = 20      # ... and at most this many
    pullback_max_bars: int = 20   # bars after the touch without a resumption -> timeout
    pvr_thin_max: float = 0.80    # pullback leg volume / reference <= this = thin
    pvr_heavy_min: float = 1.20   # >= this = high-volume pullback (rejected until a restart)
    zone_in_std: float = 0.3      # long zone upper edge: VWAP + 0.3 std (short: lower edge)
    zone_out_std: float = 0.2     # long zone lower edge: VWAP - 0.2 std (short: upper edge)
    resume_max_age: int = 3       # bars since the pullback extreme
    slope_min: float = 0.50       # close-to-close ATR per bar
    chase_max_std: float = 0.5    # close must be within this many std of VWAP
    stop_atr_mult: float = 1.5
    stop_structure_atr: float = 0.5
    target_1_r: float = 1.0
    cooldown_bars: int = 15
    max_signals_per_day: int = 2
    feed_gap_seconds: float = 300.0
    # Layer 1: net aggression during the pullback leg, (buy - sell) / (buy + sell) in [-1, 1].
    # A long pullback with sellers hitting more than this share is a trap and is rejected.
    pullback_delta_min: float = -0.30
    # Layer 3: tick velocity over the last `tick_velocity_window_s` seconds of prints,
    # in ATR per minute in the trade's direction.
    tick_velocity_window_s: int = 60
    tick_velocity_min_atr_per_min: float = 0.25
    # Layer 2: top-of-book imbalance (bid - ask) / (bid + ask) over the last window, in the
    # trade's direction (bid-heavy for longs, ask-heavy for shorts).
    book_window_s: int = 30
    book_imbalance_min: float = 0.10
    # part 2 measures (Layer 3 stream); enforced only when named in `enforced_gates`
    impulse_delta_min: float = 0.15        # buyers hit the ask on the impulse bar
    resumption_delta_min: float = 0.10     # and again on the resumption bars
    rolling_window_s: int = 1800           # flow over the last 30 minutes agrees with the trend
    enforced_gates: Tuple[str, ...] = ()
    # add-ons, enforced when addons_enforced (operator decision 2026-09-27: live from day one)
    addons_enforced: bool = True
    session_delta_min_minutes: float = 30.0
    spread_ratio_max: float = 1.5
    spread_abs_max_bps: float = 30.0
    hvn_support_below_atr: float = 0.25
    hvn_support_above_atr: float = 0.50


@dataclass
class V2SymbolState:
    """Everything the machine knows about one symbol today. Fully rebuildable from `bars`."""
    session_date: Optional[str] = None
    bars: List[BarEvent] = field(default_factory=list)
    state: str = "IDLE"
    side: Optional[str] = None          # LONG or SHORT once in IMPULSE
    impulse_i: int = -1
    ref: float = 0.0                    # frozen reference mean volume
    touch_i: int = -1
    leg_mean_vol: float = 0.0
    pvr: float = 0.0
    ext_i: int = -1                     # pullback low (long) or high (short) index
    blocked_until_restart: bool = False # HIGH_VOLUME_PULLBACK
    cooldown_until_i: int = -1          # emission blocked while i <= this
    admitted_today: int = 0             # signals that passed admission (main.py reports)
    emitted_today: int = 0
    pullback_delta: Optional[float] = None  # Layer 1 aggression ratio over the leg (at touch)
    pullback_delta_detail: Optional[Dict[str, Any]] = None
    gap_i: int = -1                     # index of the first bar after the last feed gap
    last_emitted_i: int = -1
    impulse_delta: Optional[float] = None
    impulse_delta_detail: Optional[Dict[str, Any]] = None
    # incremental features
    cum_pv: float = 0.0
    cum_v: float = 0.0
    cum_pv2: float = 0.0
    atr: Optional[float] = None
    tr_seed: List[float] = field(default_factory=list)
    prev_close: Optional[float] = None
    last_ts: Optional[datetime] = None
    # last computed values (research)
    vwap: float = 0.0
    std: float = 0.0


def _et(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET_TZ)


def _finite(*values: float) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)


def _update_features(st: V2SymbolState, bar: BarEvent) -> None:
    """Fold one accepted bar into VWAP/std/ATR. Called for every accepted bar."""
    v = float(bar.volume)
    tp = (bar.high + bar.low + bar.close) / 3.0
    st.cum_pv += tp * v
    st.cum_v += v
    st.cum_pv2 += tp * tp * v
    if st.cum_v > 0:
        st.vwap = st.cum_pv / st.cum_v
        st.std = math.sqrt(max(0.0, st.cum_pv2 / st.cum_v - st.vwap * st.vwap))
    tr = bar.high - bar.low if st.prev_close is None else max(
        bar.high - bar.low, abs(bar.high - st.prev_close), abs(bar.low - st.prev_close)
    )
    st.prev_close = bar.close
    if st.atr is None:
        st.tr_seed.append(tr)
        if len(st.tr_seed) >= 14:
            st.atr = sum(st.tr_seed) / 14.0
            st.tr_seed = []
    else:
        st.atr = (st.atr * 13.0 + tr) / 14.0


def _event(st: V2SymbolState, symbol: str, i: int, bar: BarEvent, name: str,
           from_state: str, to_state: str, **detail: Any) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "i": i,
        "bar_ts": bar.timestamp,
        "event": name,
        "from_state": from_state,
        "to_state": to_state,
        "side": st.side,
        "detail": detail,
    }


def evaluate_bar(
    st: V2SymbolState,
    bar: BarEvent,
    p: V2Params,
    *,
    emission_allowed: bool,
    vix_stop_mult: float,
    strategy_id: str = STRATEGY_ID,
    tape: Optional[Any] = None,
    tick_gates: bool = True,
) -> Tuple[List[Dict[str, Any]], Optional[SignalEvent]]:
    """Process one bar. Mutates `st`. Returns (events, signal or None).

    Deterministic in the bar sequence and the tape contents. With `tick_gates` the
    Layer 1-3 checks read `tape` (TickTape or a test double with the same methods) and
    fail closed when it has no data; `tick_gates=False` is for rebuilding the machine
    from stored bars after a restart, where no tape history exists.
    """
    events: List[Dict[str, Any]] = []
    symbol = bar.symbol.upper()
    ts_et = _et(bar.timestamp)
    t = ts_et.time()
    i = len(st.bars)  # index this bar will get if accepted

    # ---- bar policy -------------------------------------------------------------
    if not _finite(bar.open, bar.high, bar.low, bar.close, float(bar.volume)) or bar.volume < 0 \
            or bar.high < bar.low or bar.low <= 0 or bar.open <= 0 or bar.close <= 0 \
            or not (bar.low <= bar.open <= bar.high) or not (bar.low <= bar.close <= bar.high) \
            or isinstance(bar.volume, bool) or bar.volume != int(bar.volume):
        events.append(_event(st, symbol, i, bar, "BAD_BAR", st.state, st.state))
        return events, None
    if t < SESSION_OPEN or t >= SESSION_CLOSE:
        return events, None
    day = ts_et.date().isoformat()
    if st.session_date is None:
        st.session_date = day
    elif day < st.session_date:
        # A bar from an earlier session can never replace today's state or its budget.
        events.append(_event(st, symbol, i, bar, "LATE_BAR", st.state, st.state, session=day))
        return events, None
    elif day != st.session_date:
        # The wrapper starts a fresh state per session; defend anyway so days never mix.
        st.__init__(session_date=day)
        i = 0
    if bar.timestamp.tzinfo is None:
        bar = BarEvent(symbol=bar.symbol, open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                       volume=bar.volume, timestamp=bar.timestamp.replace(tzinfo=timezone.utc),
                       num_trades=bar.num_trades, vwap=bar.vwap)
    if st.last_ts is not None:
        if bar.timestamp == st.last_ts:
            events.append(_event(st, symbol, i, bar, "DUP_BAR", st.state, st.state))
            return events, None
        if bar.timestamp < st.last_ts:
            events.append(_event(st, symbol, i, bar, "LATE_BAR", st.state, st.state))
            return events, None
        if (bar.timestamp - st.last_ts).total_seconds() > p.feed_gap_seconds:
            # The reference window and the machine both need contiguous history again.
            events.append(_event(st, symbol, i, bar, "FEED_GAP", st.state, "IDLE",
                                 gap_seconds=(bar.timestamp - st.last_ts).total_seconds()))
            _to_idle(st)
            st.gap_i = i

    # ---- accept the bar ----------------------------------------------------------
    st.bars.append(bar)
    st.last_ts = bar.timestamp
    _update_features(st, bar)
    bars = st.bars

    if bar.volume == 0:
        events.append(_event(st, symbol, i, bar, "ZERO_VOLUME_BAR", st.state, st.state))
        return events, None

    # After the window an active setup is discarded and nothing is evaluated. The window is
    # judged on the bar's completion time (start + 1 minute), which is when an order would go.
    t_end = (ts_et + timedelta(minutes=1)).time() if ts_et.hour < 23 else t
    if t_end > WINDOW_CLOSE or t_end == WINDOW_CLOSE:
        if st.state != "IDLE":
            events.append(_event(st, symbol, i, bar, "WINDOW_CLOSED", st.state, "IDLE"))
            _to_idle(st)
        return events, None

    # ---- features ----------------------------------------------------------------
    std = st.std if st.std > 0.001 else (st.atr or 0.0)
    atr = st.atr
    ref_ok = (i - p.ref_bars) >= max(p.ref_min_start, st.gap_i)
    if not (isinstance(vix_stop_mult, (int, float)) and math.isfinite(vix_stop_mult) and vix_stop_mult > 0):
        vix_stop_mult = 1.0
    ref = 0.0
    if ref_ok:
        vols = [float(b.volume) for b in bars[i - p.ref_bars:i]]
        ref = sum(vols) / len(vols)
    if atr is None or atr <= 0 or std <= 0 or not ref_ok or ref <= 0 or not _finite(atr, std, ref):
        events.append(_event(st, symbol, i, bar, "FEATURE_UNAVAILABLE", st.state, st.state,
                             atr=atr, std=std, ref=ref if ref_ok else None))
        return events, None
    vwap = st.vwap

    # ---- restart check (beats any state step on the same bar) ---------------------
    window = bars[i - p.ref_bars:i]
    new_high = bar.high > max(b.high for b in window)
    new_low = bar.low < min(b.low for b in window)
    if new_high and new_low:
        if st.state != "IDLE":
            events.append(_event(st, symbol, i, bar, "AMBIGUOUS_BAR", st.state, "IDLE"))
        _to_idle(st)
        return events, None
    restart_side: Optional[str] = None
    if new_high and bar.close > vwap:
        restart_side = "LONG"
    elif new_low and bar.close < vwap:
        restart_side = "SHORT"
    if restart_side is not None:
        from_state = st.state
        _to_idle(st)
        st.state = "IMPULSE"
        st.side = restart_side
        st.impulse_i = i
        st.ref = ref
        st.blocked_until_restart = False
        sgn_r = 1.0 if restart_side == "LONG" else -1.0
        imp = None
        if tick_gates and tape is not None:
            imp = _ok(_safe(tape.delta, symbol, _ns(bar.timestamp), _ns(bar.timestamp) + 60 * NS))
        st.impulse_delta = float(imp["delta_ratio"]) if imp else None
        st.impulse_delta_detail = ({k: imp[k] for k in ("buy_vol", "sell_vol", "unknown_vol", "n_trades", "quote_share")}
                                   if imp else None)
        events.append(_event(st, symbol, i, bar, "IMPULSE", from_state, "IMPULSE", ref=ref,
                             impulse_price=bar.high if restart_side == "LONG" else bar.low,
                             impulse_delta=st.impulse_delta, pre_empted=from_state in ("PULLBACK", "RESUMING")))
        if "IMPULSE_DELTA" in p.enforced_gates and tick_gates:
            if imp is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "IMPULSE", "IDLE", where="impulse_delta"))
                _to_idle(st)
            elif sgn_r * st.impulse_delta < p.impulse_delta_min:
                events.append(_event(st, symbol, i, bar, "IMPULSE_NOT_AGGRESSIVE", "IMPULSE", "IDLE",
                                     delta_ratio=st.impulse_delta))
                _to_idle(st)
        return events, None

    if st.state == "IDLE":
        return events, None

    is_long = st.side == "LONG"
    sgn = 1.0 if is_long else -1.0

    # ---- IMPULSE: wait for the zone touch --------------------------------------
    if st.state == "IMPULSE":
        since = i - st.impulse_i
        if is_long:
            touched = bar.low <= vwap + p.zone_in_std * std and bar.high >= vwap - p.zone_out_std * std
        else:
            touched = bar.high >= vwap - p.zone_in_std * std and bar.low <= vwap + p.zone_out_std * std
        if touched:
            if since < p.touch_min_bars:
                events.append(_event(st, symbol, i, bar, "TOUCH_TOO_EARLY", "IMPULSE", "IDLE", since=since))
                _to_idle(st)
                return events, None
            leg = bars[st.impulse_i + 1:i + 1]
            leg_mean = sum(float(b.volume) for b in leg) / len(leg)
            pvr = leg_mean / st.ref if st.ref > 0 else float("inf")
            st.touch_i = i
            st.leg_mean_vol = leg_mean
            st.pvr = pvr
            if not math.isfinite(pvr):
                events.append(_event(st, symbol, i, bar, "FEATURE_UNAVAILABLE", "IMPULSE", "IDLE", pvr=None))
                _to_idle(st)
                return events, None
            if pvr >= p.pvr_heavy_min:
                events.append(_event(st, symbol, i, bar, "HIGH_VOLUME_PULLBACK", "IMPULSE", "IDLE",
                                     pvr=pvr, leg_bars=len(leg)))
                _to_idle(st)
                st.blocked_until_restart = True
                return events, None
            if pvr > p.pvr_thin_max:
                events.append(_event(st, symbol, i, bar, "PVR_NOT_THIN", "IMPULSE", "IDLE", pvr=pvr, leg_bars=len(leg)))
                _to_idle(st)
                return events, None
            if tick_gates:
                # Layer 1: who was hitting during the pullback leg (impulse bar end .. touch bar end).
                d = _ok(_safe(tape.delta, symbol, _ns(leg[0].timestamp), _ns(bar.timestamp) + 60 * NS)) if tape is not None else None
                if d is None:
                    events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "IMPULSE", "IDLE", where="pullback_delta"))
                    _to_idle(st)
                    return events, None
                ratio = float(d["delta_ratio"])
                st.pullback_delta = ratio
                st.pullback_delta_detail = {k: d[k] for k in ("buy_vol", "sell_vol", "unknown_vol", "n_trades", "classified_share")}
                if sgn_side(st.side) * ratio < p.pullback_delta_min:
                    events.append(_event(st, symbol, i, bar, "AGGRESSIVE_PULLBACK", "IMPULSE", "IDLE",
                                         pvr=pvr, delta_ratio=ratio, buy_vol=d["buy_vol"], sell_vol=d["sell_vol"]))
                    _to_idle(st)
                    st.blocked_until_restart = True
                    return events, None
            st.state = "PULLBACK"
            # The pullback extreme is the lowest low (highest high) of the WHOLE leg, so a bar
            # that gapped through the zone without intersecting it still sets the structure.
            leg_idx = range(st.impulse_i + 1, i + 1)
            st.ext_i = (min(leg_idx, key=lambda k: bars[k].low) if is_long else max(leg_idx, key=lambda k: bars[k].high))
            events.append(_event(st, symbol, i, bar, "PULLBACK", "IMPULSE", "PULLBACK", pvr=pvr, leg_bars=len(leg),
                                 delta_ratio=st.pullback_delta, ext_i=st.ext_i))
            return events, None
        if since >= p.touch_max_bars:
            events.append(_event(st, symbol, i, bar, "NO_TOUCH", "IMPULSE", "IDLE", since=since))
            _to_idle(st)
        return events, None

    # ---- PULLBACK: track the extreme, wait for the first close in our direction ----
    if st.state == "PULLBACK":
        if i - st.touch_i > p.pullback_max_bars:
            events.append(_event(st, symbol, i, bar, "PULLBACK_TIMEOUT", "PULLBACK", "IDLE"))
            _to_idle(st)
            return events, None
        ext = bars[st.ext_i]
        if (is_long and bar.low < ext.low) or ((not is_long) and bar.high > ext.high):
            st.ext_i = i
            events.append(_event(st, symbol, i, bar, "NEW_EXTREME", "PULLBACK", "PULLBACK"))
            return events, None
        prev = bars[i - 1]
        if i > st.ext_i and sgn * (bar.close - prev.close) > 0:
            st.state = "RESUMING"
            events.append(_event(st, symbol, i, bar, "RESUMING", "PULLBACK", "RESUMING", age=i - st.ext_i))
            # fall through: evaluate on this same bar
        else:
            return events, None

    # ---- RESUMING: the entry test -----------------------------------------------
    if st.state == "RESUMING":
        ext = bars[st.ext_i]
        if (is_long and bar.low < ext.low) or ((not is_long) and bar.high > ext.high):
            st.ext_i = i
            st.state = "PULLBACK"
            events.append(_event(st, symbol, i, bar, "NEW_EXTREME", "RESUMING", "PULLBACK"))
            return events, None
        age = i - st.ext_i
        if age > p.resume_max_age:
            events.append(_event(st, symbol, i, bar, "RESUMPTION_TOO_OLD", "RESUMING", "IDLE", age=age))
            _to_idle(st)
            return events, None
        prev = bars[i - 1]
        slope = sgn * (bar.close - ext.close) / age / atr
        chase = sgn * (bar.close - vwap) / std
        advance_atr = sgn * (bar.close - ext.close) / atr
        # ---- measurement record: every resumption evaluation, before any gate decides -------------
        # One snapshot, queried once, complete-and-finite only; the gates below use these same values.
        bar_end_ns = _ns(bar.timestamp) + 60 * NS
        m_vel = m_book = m_res = m_roll = m_leg = None
        m_regime = None
        if tick_gates and tape is not None:
            m_vel = _ok(_safe(tape.velocity, symbol, bar_end_ns, p.tick_velocity_window_s), "per_second")
            m_book = _ok(_safe(tape.book_imbalance, symbol, bar_end_ns, p.book_window_s), "imbalance")
            m_res = _ok(_safe(tape.delta, symbol, _ns(ext.timestamp) + 60 * NS, bar_end_ns))
            m_roll = _ok(_safe(tape.rolling_delta, symbol, bar_end_ns, p.rolling_window_s))
            if st.ext_i > st.impulse_i:
                m_leg = _ok(_safe(tape.delta, symbol, _ns(bars[st.impulse_i + 1].timestamp), _ns(bars[st.ext_i].timestamp) + 60 * NS))
        m_sess = m_spread = None
        if tick_gates and tape is not None:
            m_sess = _ok(_safe(tape.session_delta, symbol, bar_end_ns, p.session_delta_min_minutes), "delta")
            m_spread = _ok(_safe(tape.spread_stats, symbol, bar_end_ns), "ratio")
        prof = _safe(PROFILE.get, symbol, st.session_date) if PROFILE is not None else None
        m_support = m_overhead = None
        profile_error: Optional[str] = None
        ext_price = ext.low if is_long else ext.high
        levels = executable_levels(bar.close, ext_price, atr, vix_stop_mult, is_long, p)
        _target = levels["target"] if levels else None
        if prof is not None and levels is not None:
            # profile queries must fail CLOSED: an error is recorded as unavailable, never as "clear"
            try:
                m_support = prof.support_node(ext_price, atr, is_long, p.hvn_support_below_atr, p.hvn_support_above_atr)
                m_overhead = prof.path_obstacle(bar.close, _target, is_long, m_support)
            except Exception as exc:
                profile_error = f"{type(exc).__name__}: {exc}"
                m_support = m_overhead = None
        if REGIME is not None:
            t30 = bar.timestamp - timedelta(minutes=30)
            past = next((b.close for b in reversed(bars) if b.timestamp <= t30), None)
            ret30 = (bar.close / past - 1.0) if past else None
            m_regime = _safe(REGIME.evaluate, symbol, is_long, bar.timestamp + timedelta(minutes=1), ret30)
        events.append(_event(st, symbol, i, bar, "RESUMPTION_MEASURED", "RESUMING", "RESUMING", age=age,
                             up_close=bool(sgn * (bar.close - prev.close) > 0), slope=slope, chase_std=chase,
                             advance_atr=advance_atr, vwap=vwap, std=std, atr=atr, ext_close=ext.close,
                             impulse_delta=st.impulse_delta,
                             pullback_delta=(float(m_leg["delta_ratio"]) if m_leg else st.pullback_delta),
                             resumption_delta=(float(m_res["delta_ratio"]) if m_res else None),
                             rolling_delta=(float(m_roll["delta_ratio"]) if m_roll else None),
                             rolling_signed=(m_roll["delta"] if m_roll else None),
                             tick_velocity_atr_per_min=((sgn * float(m_vel["per_second"]) * 60.0 / atr) if m_vel else None),
                             book_imbalance=(float(m_book["imbalance"]) if m_book else None),
                             session_delta=(m_sess["delta"] if m_sess else None),
                             session_delta_ratio=(float(m_sess["delta_ratio"]) if m_sess else None),
                             session_delta_partial=(bool(m_sess["partial"]) if m_sess else None),
                             session_delta_since_ns=(m_sess["since_ns"] if m_sess else None),
                             session_delta_cutoff_ns=(m_sess["cutoff_ns"] if m_sess else None),
                             profile_error=profile_error, executable_target=_target,
                             spread_now_bps=(float(m_spread["spread_now_bps"]) if m_spread else None),
                             spread_ref_bps=(float(m_spread["spread_ref_bps"]) if m_spread else None),
                             spread_ratio=(float(m_spread["ratio"]) if m_spread else None),
                             profile_sessions=(len(prof.sessions) if prof else None),
                             hvn_support=(m_support.as_dict() if m_support else None),
                             hvn_overhead=(m_overhead.as_dict() if m_overhead else None),
                             regime=({"failed": m_regime["failed"], "unavailable": m_regime["unavailable"],
                                      "sector_rs_30m": m_regime["measures"].get("sector_rs_30m"),
                                      "dollar_30m": (m_regime["measures"].get("dollar_return_30m") or {}).get("return"),
                                      "ief_30m": (m_regime["measures"].get("ief_return_30m") or {}).get("return")}
                                     if m_regime else None)))
        if sgn * (bar.close - prev.close) <= 0:
            events.append(_event(st, symbol, i, bar, "NO_UP_CLOSE", "RESUMING", "RESUMING", age=age))
            return events, None
        if slope < p.slope_min:
            events.append(_event(st, symbol, i, bar, "SLOPE_TOO_SLOW", "RESUMING", "RESUMING", age=age, slope=slope))
            return events, None
        if chase > p.chase_max_std:
            events.append(_event(st, symbol, i, bar, "CHASED", "RESUMING", "IDLE", age=age, chase_std=chase))
            _to_idle(st)
            return events, None
        # The leg is re-measured through its final extreme: a quiet first touch followed by a
        # heavy, aggressive slide to a lower low must not enter on the early numbers.
        full_leg = bars[st.impulse_i + 1:st.ext_i + 1]
        if full_leg and st.ref > 0:
            full_pvr = (sum(float(b.volume) for b in full_leg) / len(full_leg)) / st.ref
            if not math.isfinite(full_pvr) or full_pvr > p.pvr_thin_max:
                events.append(_event(st, symbol, i, bar, "PVR_NOT_THIN", "RESUMING", "IDLE", pvr=full_pvr,
                                     leg_bars=len(full_leg), where="full_leg"))
                _to_idle(st)
                return events, None
            st.pvr = full_pvr
        tick_vel: Optional[Dict[str, Any]] = None
        book: Optional[Dict[str, Any]] = None
        vel_atr_min: Optional[float] = None
        if tick_gates:
            # Layer 1 again, over the whole pullback through the extreme bar's end (from the snapshot).
            d = m_leg
            if d is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="full_leg_delta"))
                _to_idle(st)
                return events, None
            st.pullback_delta = float(d["delta_ratio"])
            st.pullback_delta_detail = {k: d[k] for k in ("buy_vol", "sell_vol", "unknown_vol", "n_trades", "classified_share")}
            if sgn * st.pullback_delta < p.pullback_delta_min:
                events.append(_event(st, symbol, i, bar, "AGGRESSIVE_PULLBACK", "RESUMING", "IDLE",
                                     delta_ratio=st.pullback_delta, where="full_leg"))
                _to_idle(st)
                return events, None
            # Layer 3: velocity on the prints' own timestamps over the last window (complete window only).
            tick_vel = m_vel
            if tick_vel is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="velocity"))
                _to_idle(st)
                return events, None
            vel_atr_min = sgn * float(tick_vel["per_second"]) * 60.0 / atr
            if not math.isfinite(vel_atr_min):
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="velocity_nan"))
                _to_idle(st)
                return events, None
            if vel_atr_min < p.tick_velocity_min_atr_per_min:
                events.append(_event(st, symbol, i, bar, "TICK_VELOCITY_LOW", "RESUMING", "RESUMING", age=age,
                                     tick_velocity_atr_per_min=vel_atr_min, n_trades=tick_vel["n_trades"]))
                return events, None
            # Layer 2: the book must lean the trade's way at the inside (covered window only).
            book = m_book
            if book is None:
                events.append(_event(st, symbol, i, bar, "BOOK_UNAVAILABLE", "RESUMING", "IDLE"))
                _to_idle(st)
                return events, None
            if not math.isfinite(float(book["imbalance"])):
                events.append(_event(st, symbol, i, bar, "BOOK_UNAVAILABLE", "RESUMING", "IDLE", where="nan"))
                _to_idle(st)
                return events, None
            if sgn * float(book["imbalance"]) < p.book_imbalance_min:
                events.append(_event(st, symbol, i, bar, "BOOK_AGAINST", "RESUMING", "RESUMING", age=age,
                                     imbalance=book["imbalance"], quotes=book["quotes"]))
                return events, None
        # ---- part 2 measures: recorded on every candidate, gates only when enforced ----------
        res_d, roll_d = m_res, m_roll
        if "RESUMPTION_DELTA" in p.enforced_gates and tick_gates:
            if res_d is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="resumption_delta"))
                _to_idle(st)
                return events, None
            if sgn * float(res_d["delta_ratio"]) < p.resumption_delta_min:
                events.append(_event(st, symbol, i, bar, "RESUMPTION_NOT_AGGRESSIVE", "RESUMING", "RESUMING",
                                     age=age, delta_ratio=float(res_d["delta_ratio"])))
                return events, None
        if "ROLLING_DELTA" in p.enforced_gates and tick_gates:
            if roll_d is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="rolling_delta"))
                _to_idle(st)
                return events, None
            if sgn * float(roll_d["delta"]) <= 0:
                events.append(_event(st, symbol, i, bar, "CUM_DELTA_AGAINST", "RESUMING", "RESUMING", age=age,
                                     rolling_signed=roll_d["delta"]))
                return events, None
        if p.addons_enforced and tick_gates:
            # Add-on A: net session flow must agree with the trade.
            if m_sess is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="session_delta"))
                _to_idle(st)
                return events, None
            if sgn * float(m_sess["delta"]) <= 0:
                events.append(_event(st, symbol, i, bar, "SESSION_DELTA_AGAINST", "RESUMING", "RESUMING", age=age,
                                     session_delta=m_sess["delta"], session_delta_ratio=float(m_sess["delta_ratio"])))
                return events, None
            # Add-on B: the spread must not be widening (liquidity drying up) and must be tight in absolute terms.
            if m_spread is None:
                events.append(_event(st, symbol, i, bar, "TICK_UNAVAILABLE", "RESUMING", "IDLE", where="spread"))
                _to_idle(st)
                return events, None
            if float(m_spread["ratio"]) > p.spread_ratio_max or float(m_spread["spread_now_bps"]) > p.spread_abs_max_bps:
                events.append(_event(st, symbol, i, bar, "SPREAD_WIDE", "RESUMING", "RESUMING", age=age,
                                     spread_now_bps=float(m_spread["spread_now_bps"]), spread_ref_bps=float(m_spread["spread_ref_bps"]),
                                     ratio=float(m_spread["ratio"])))
                return events, None
        if p.addons_enforced and tick_gates:
            # Add-on C: the dip must sit on prior liquidity and the road to 1R must be clear of a node.
            if levels is None:
                events.append(_event(st, symbol, i, bar, "STOP_TOO_WIDE", "RESUMING", "IDLE", entry=bar.close, before_profile=True))
                _to_idle(st)
                return events, None
            if prof is None or profile_error is not None:
                events.append(_event(st, symbol, i, bar, "PROFILE_UNAVAILABLE", "RESUMING", "IDLE", error=profile_error))
                _to_idle(st)
                return events, None
            if m_support is None:
                events.append(_event(st, symbol, i, bar, "HVN_NO_SUPPORT", "RESUMING", "IDLE", age=age,
                                     extreme=(ext.low if is_long else ext.high), nodes=len(prof.nodes)))
                _to_idle(st)
                return events, None
            if m_overhead is not None:
                events.append(_event(st, symbol, i, bar, "HVN_OVERHEAD", "RESUMING", "RESUMING", age=age,
                                     entry=bar.close, target=_target, node=m_overhead.as_dict()))
                return events, None
        if t_end < WINDOW_OPEN:
            events.append(_event(st, symbol, i, bar, "WINDOW_CLOSED", "RESUMING", "IDLE", before_open=True))
            _to_idle(st)
            return events, None
        if not emission_allowed or i <= st.cooldown_until_i or st.admitted_today >= p.max_signals_per_day:
            events.append(_event(st, symbol, i, bar, "EMISSION_DISABLED", "RESUMING", "IDLE",
                                 cooldown_until_i=st.cooldown_until_i, admitted_today=st.admitted_today,
                                 operator_allowed=emission_allowed))
            _to_idle(st)
            return events, None

        entry = bar.close
        structure = sgn * (entry - (ext.low if is_long else ext.high)) + p.stop_structure_atr * atr
        if levels is None:
            raw_dist = max(p.stop_atr_mult * atr * vix_stop_mult, structure, MIN_STOP_DISTANCE_PCT * entry)
            events.append(_event(st, symbol, i, bar, "STOP_TOO_WIDE", "RESUMING", "IDLE", entry=entry, dist=raw_dist))
            _to_idle(st)
            return events, None
        stop, dist, tp1 = levels["stop"], levels["dist"], levels["target"]
        signal = SignalEvent(
            symbol=symbol,
            side=OrderSide.BUY if is_long else OrderSide.SELL,
            order_type=OrderType.MARKET,
            entry_price=entry,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp1,  # runner has no second target (TRAIL_ONLY)
            strategy_id=strategy_id,
            confidence=0.75,
            reason=(
                f"RIDE_THE_TREND_V2_{'LONG' if is_long else 'SHORT'}: thin pullback pvr={st.pvr:.2f}, "
                f"resumption slope={slope:.2f} ATR/bar age={age}, VWAP {vwap:.2f}"
            ),
            timestamp=bar.timestamp,
            stop_is_final=True,
        )
        feats = {
            "version": VERSION, "policy_id": POLICY_ID, "i": i,
            "vwap": round(vwap, 4), "std": round(std, 4), "atr": round(atr, 4), "ref_mean_vol": round(st.ref, 2),
            "impulse_i": st.impulse_i, "touch_i": st.touch_i, "ext_i": st.ext_i,
            "leg_bars": st.touch_i - st.impulse_i, "leg_mean_vol": round(st.leg_mean_vol, 2), "pvr": round(st.pvr, 4),
            "post_touch_vol_ratio": round((sum(float(b.volume) for b in bars[st.touch_i + 1:i + 1]) / max(1, i - st.touch_i)) / st.ref, 4) if i > st.touch_i else None,
            "resume_vol_ratio": round((sum(float(b.volume) for b in bars[st.ext_i + 1:i + 1]) / age) / st.leg_mean_vol, 4) if st.leg_mean_vol > 0 else None,
            "age": age, "slope_atr_per_bar": round(slope, 4), "chase_std": round(chase, 4),
            "pullback_extreme": ext.low if is_long else ext.high,
            "stop_dist": round(dist, 4), "stop_dist_pct": round(dist / entry, 5),
            "stop_atr_candidate": round(p.stop_atr_mult * atr * vix_stop_mult, 4),
            "stop_structure_candidate": round(structure, 4), "vix_stop_mult": vix_stop_mult,
            "bounce_wick_ratio": round(((min(bar.open, bar.close) - bar.low) if is_long else (bar.high - max(bar.open, bar.close))) / max(0.01, bar.high - bar.low), 4),
            "bounce_vol_ratio_sma10": round(float(bar.volume) / (sum(float(b.volume) for b in bars[max(0, i - 10):i]) / max(1, min(10, i))), 4) if i > 0 else None,
            "day_move_pct": round(bar.close / bars[0].open - 1, 5),
            "layer1_pullback_delta_ratio": st.pullback_delta,
            "layer1_pullback_delta": st.pullback_delta_detail,
            "layer2_book_imbalance": (round(float(book["imbalance"]), 4) if book else None),
            "layer2_book": ({k: book.get(k) for k in ("bid", "ask", "bid_size", "ask_size", "spread", "quotes")} if book else None),
            "layer3_tick_velocity_atr_per_min": (round(vel_atr_min, 4) if vel_atr_min is not None else None),
            "layer3_tick_velocity": ({k: tick_vel.get(k) for k in ("price_change", "elapsed_s", "per_second", "n_trades")} if tick_vel else None),
            "tick_gates": bool(tick_gates),
            "layer3_impulse_delta_ratio": st.impulse_delta,
            "layer3_impulse_delta": st.impulse_delta_detail,
            "layer3_resumption_delta_ratio": (round(float(res_d["delta_ratio"]), 4) if res_d else None),
            "layer3_resumption_delta": ({k: res_d[k] for k in ("buy_vol", "sell_vol", "n_trades", "quote_share")} if res_d else None),
            "layer3_rolling_delta_ratio": (round(float(roll_d["delta_ratio"]), 4) if roll_d else None),
            "layer3_rolling_signed_volume": (roll_d["delta"] if roll_d else None),
            "layer3_advance_since_extreme_atr": round(advance_atr, 4),
            "enforced_gates": list(p.enforced_gates),
            "addons_enforced": bool(p.addons_enforced),
            "addon_session_delta": ({k: m_sess[k] for k in ("delta", "delta_ratio", "quote_share", "minutes_continuous", "partial", "since_ns", "cutoff_ns")} if m_sess else None),
            "addon_spread": ({k: m_spread[k] for k in ("spread_now_bps", "spread_ref_bps", "ratio", "ref_seconds")} if m_spread else None),
            "addon_hvn_support": (m_support.as_dict() if m_support else None),
            "addon_hvn_overhead": (m_overhead.as_dict() if m_overhead else None),
            "addon_profile": ({"sessions": prof.sessions, "nodes": len(prof.nodes), "bucket_width": round(prof.bucket_width, 4)} if prof else None),
            "params": {"pvr_thin_max": p.pvr_thin_max, "pvr_heavy_min": p.pvr_heavy_min, "slope_min": p.slope_min,
                       "resume_max_age": p.resume_max_age, "chase_max_std": p.chase_max_std,
                       "stop_atr_mult": p.stop_atr_mult, "stop_structure_atr": p.stop_structure_atr,
                       "target_1_r": p.target_1_r, "pullback_delta_min": p.pullback_delta_min,
                       "tick_velocity_min_atr_per_min": p.tick_velocity_min_atr_per_min,
                       "book_imbalance_min": p.book_imbalance_min, "impulse_delta_min": p.impulse_delta_min,
                       "resumption_delta_min": p.resumption_delta_min, "rolling_window_s": p.rolling_window_s,
                       "spread_ratio_max": p.spread_ratio_max, "spread_abs_max_bps": p.spread_abs_max_bps,
                       "session_delta_min_minutes": p.session_delta_min_minutes,
                       "hvn_support_below_atr": p.hvn_support_below_atr, "hvn_support_above_atr": p.hvn_support_above_atr},
        }
        attach_features(signal, lambda: feats)
        events.append(_event(st, symbol, i, bar, "SIGNAL", "RESUMING", "IDLE", entry=entry, stop=stop, tp1=tp1,
                             pvr=st.pvr, slope=slope, age=age))
        st.emitted_today += 1
        st.last_emitted_i = i
        # The cooldown starts when main.py admits the order (notify_admitted); a signal the
        # admission gates refuse does not burn the next 15 bars.
        _to_idle(st)
        return events, signal

    return events, None


def sgn_side(side: Optional[str]) -> float:
    return 1.0 if side == "LONG" else -1.0


def executable_levels(entry: float, extreme: float, atr: float, vix_stop_mult: float, is_long: bool,
                      p: "V2Params") -> Optional[Dict[str, float]]:
    """The stop and first target exactly as the signal will carry them (rounded away from entry, floor
    and cap applied). None when the stop would be too wide (or otherwise invalid)."""
    sgn = 1.0 if is_long else -1.0
    structure = sgn * (entry - extreme) + p.stop_structure_atr * atr
    dist = max(p.stop_atr_mult * atr * vix_stop_mult, structure, MIN_STOP_DISTANCE_PCT * entry)
    if not math.isfinite(dist) or dist > MAX_STOP_DISTANCE_PCT * entry:
        return None
    stop = math.floor((entry - dist) * 10000) / 10000 if is_long else math.ceil((entry + dist) * 10000) / 10000
    dist = abs(entry - stop)
    if not (MIN_STOP_DISTANCE_PCT * entry * (1 - 1e-9) <= dist <= MAX_STOP_DISTANCE_PCT * entry) or stop <= 0:
        return None
    return {"stop": stop, "dist": dist, "target": round(entry + sgn * p.target_1_r * dist, 4)}


def _to_idle(st: V2SymbolState) -> None:
    st.state = "IDLE"
    st.side = None
    st.impulse_i = -1
    st.ref = 0.0
    st.touch_i = -1
    st.leg_mean_vol = 0.0
    st.pvr = 0.0
    st.ext_i = -1
    st.blocked_until_restart = False
    st.pullback_delta = None
    st.pullback_delta_detail = None
    st.impulse_delta = None
    st.impulse_delta_detail = None


class VWAPPullbackV2Strategy(Strategy):
    """Ride the Trend v2. Keeps strategy_id "vwap_pullback" so checkpoints restore."""

    def __init__(
        self,
        strategy_id: str = STRATEGY_ID,
        name: str = "Ride the Trend v2 (VWAP structure, thin pullback, resumption speed)",
        mode: str = "v2_live",
        excluded_symbols: Optional[List[str]] = None,
        pvr_thin_max: float = 0.80,
        pvr_heavy_min: float = 1.20,
        slope_min: float = 0.50,
        resume_max_age: int = 3,
        chase_max_std: float = 0.5,
        stop_atr_mult: float = 1.5,
        stop_structure_atr: float = 0.5,
        target_1_r: float = 1.0,
        target_2_r: float = 1.0,
        cooldown_bars: int = 15,
        max_signals_per_day: int = 2,
        require_tick_layers: bool = True,
        pullback_delta_min: float = -0.30,
        tick_velocity_min_atr_per_min: float = 0.25,
        book_imbalance_min: float = 0.10,
        enforced_gates: Optional[List[str]] = None,
        impulse_delta_min: float = 0.15,
        resumption_delta_min: float = 0.10,
        addons_enforced: Optional[bool] = None,   # None = follow require_tick_layers (add-ons need the tape)
        spread_ratio_max: float = 1.5,
        spread_abs_max_bps: float = 30.0,
        session_delta_min_minutes: float = 30.0,
        hvn_support_below_atr: float = 0.25,
        hvn_support_above_atr: float = 0.50,
    ):
        super().__init__(strategy_id=strategy_id, name=name)
        if addons_enforced is None:
            addons_enforced = bool(require_tick_layers)
        if addons_enforced and not require_tick_layers:
            raise ValueError("the add-on gates need require_tick_layers=True")
        self.addons_enforced = bool(addons_enforced)
        self.spread_ratio_max = spread_ratio_max
        self.spread_abs_max_bps = spread_abs_max_bps
        self.session_delta_min_minutes = session_delta_min_minutes
        self.hvn_support_below_atr = hvn_support_below_atr
        self.hvn_support_above_atr = hvn_support_above_atr
        names = [str(g).strip().upper() for g in (enforced_gates or [])]
        unknown = [g for g in names if g not in PART2_GATES]
        if unknown:
            raise ValueError(f"unknown Ride the Trend gate(s) {unknown}; valid: {PART2_GATES}")
        if names and not require_tick_layers:
            raise ValueError("enforced tape gates require require_tick_layers=True")
        self.enforced_gates: List[str] = sorted(set(names))
        self.impulse_delta_min = impulse_delta_min
        self.resumption_delta_min = resumption_delta_min
        self.require_tick_layers = bool(require_tick_layers)
        self.pullback_delta_min = pullback_delta_min
        self.tick_velocity_min_atr_per_min = tick_velocity_min_atr_per_min
        self.book_imbalance_min = book_imbalance_min
        self.version: str = VERSION
        self.policy_id: str = POLICY_ID
        self.state_schema: int = 2
        self.mode: str = mode
        self.excluded_symbols: List[str] = [s.upper() for s in (excluded_symbols or [])]
        self.pvr_thin_max = pvr_thin_max
        self.pvr_heavy_min = pvr_heavy_min
        self.slope_min = slope_min
        self.resume_max_age = resume_max_age
        self.chase_max_std = chase_max_std
        self.stop_atr_mult = stop_atr_mult
        self.stop_structure_atr = stop_structure_atr
        self.target_1_r = target_1_r
        self.target_2_r = target_2_r
        self.cooldown_bars = cooldown_bars
        self.max_signals_per_day = max_signals_per_day
        self.vix_stop_multiplier: float = 1.0   # main.py refreshes this before every bar
        self.symbol_states: Dict[str, V2SymbolState] = {}
        self.event_counts_today: Dict[str, int] = {}
        self.last_evaluated_bar: Optional[str] = None
        self.restored_from: Optional[str] = None
        self.emission_blocked_reason: Optional[str] = None  # set when a restore could not be trusted
        self.last_block_by_symbol: Dict[str, Dict[str, Any]] = {}

    # ----------------------------------------------------------------- params
    def params(self) -> V2Params:
        return V2Params(
            pvr_thin_max=self.pvr_thin_max, pvr_heavy_min=self.pvr_heavy_min, slope_min=self.slope_min,
            resume_max_age=self.resume_max_age, chase_max_std=self.chase_max_std,
            stop_atr_mult=self.stop_atr_mult, stop_structure_atr=self.stop_structure_atr,
            target_1_r=self.target_1_r, cooldown_bars=self.cooldown_bars,
            max_signals_per_day=self.max_signals_per_day,
            pullback_delta_min=self.pullback_delta_min,
            tick_velocity_min_atr_per_min=self.tick_velocity_min_atr_per_min,
            book_imbalance_min=self.book_imbalance_min,
            impulse_delta_min=self.impulse_delta_min,
            resumption_delta_min=self.resumption_delta_min,
            enforced_gates=tuple(self.enforced_gates),
            addons_enforced=self.addons_enforced,
            spread_ratio_max=self.spread_ratio_max, spread_abs_max_bps=self.spread_abs_max_bps,
            session_delta_min_minutes=self.session_delta_min_minutes,
            hvn_support_below_atr=self.hvn_support_below_atr, hvn_support_above_atr=self.hvn_support_above_atr,
        )

    def _get_state(self, symbol: str) -> V2SymbolState:
        sym = symbol.upper()
        st = self.symbol_states.get(sym)
        if not isinstance(st, V2SymbolState):
            st = V2SymbolState()
            self.symbol_states[sym] = st
        return st

    def reset_daily_stats(self) -> None:
        super().reset_daily_stats()
        self.symbol_states.clear()
        self.event_counts_today = {}
        self.last_block_by_symbol = {}

    # ----------------------------------------------------------------- bars
    def on_bar(self, bar: BarEvent) -> List[SignalEvent]:
        sym = bar.symbol.upper()
        if sym in self.excluded_symbols:
            return []
        ts_et = _et(bar.timestamp)
        if ts_et.time() < SESSION_OPEN or ts_et.time() >= SESSION_CLOSE:
            return []
        day = ts_et.date().isoformat()
        st = self._get_state(sym)
        if st.session_date is not None and day < st.session_date:
            return []  # a bar from an earlier session never touches today's state
        if st.session_date is not None and st.session_date != day:
            st = V2SymbolState()
            self.symbol_states[sym] = st
        events, signal = evaluate_bar(
            st, bar, self.params(),
            emission_allowed=self.emission_allowed(),
            vix_stop_mult=float(self.vix_stop_multiplier or 1.0),
            strategy_id=self.strategy_id,
            tape=TAPE,
            tick_gates=self.require_tick_layers,
        )
        self.last_evaluated_bar = bar.timestamp.isoformat()
        for ev in events:
            self.event_counts_today[ev["event"]] = self.event_counts_today.get(ev["event"], 0) + 1
            if ev["event"] not in ("RESUMPTION_MEASURED", "FEATURE_UNAVAILABLE", "IMPULSE", "PULLBACK", "RESUMING", "NEW_EXTREME", "SIGNAL"):
                self.last_block_by_symbol[sym] = {"event": ev["event"], "bar": bar.timestamp.isoformat(),
                                                  "detail": {k: v for k, v in (ev.get("detail") or {}).items() if isinstance(v, (int, float, str, bool)) or v is None}}
            sink = EVENT_SINK
            if sink is not None:
                try:
                    sink(dict(ev, strategy_id=self.strategy_id, policy_id=self.policy_id))
                except Exception:
                    pass  # research only; never drops a bar
        return [signal] if signal is not None else []

    def notify_admitted(self, symbol: str) -> None:
        """main.py calls this when a v2 signal was submitted as an order: daily budget and cooldown."""
        st = self._get_state(symbol)
        st.admitted_today += 1
        anchor = st.last_emitted_i if st.last_emitted_i >= 0 else len(st.bars) - 1
        st.cooldown_until_i = max(st.cooldown_until_i, anchor + self.cooldown_bars)

    def emission_allowed(self) -> bool:
        return (self.status == StrategyStatus.ACTIVE and self.mode == "v2_live"
                and not self.emission_blocked_reason)

    def rs_inputs(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Stock-side inputs for the relative-strength and regime gates (admission step)."""
        st = self.symbol_states.get(symbol.upper())
        if not isinstance(st, V2SymbolState) or not st.bars:
            return None
        bars = st.bars
        # wall-clock 30-minute return: the last bar that started at or before now - 30 minutes
        t30 = bars[-1].timestamp - timedelta(minutes=30)
        close_30m = None
        for b in reversed(bars):
            if b.timestamp <= t30:
                close_30m = b.close
                break
        return {
            "return_30m": (bars[-1].close / close_30m - 1.0) if close_30m else None,
            "open_0930": bars[0].open,
            "open_ts": bars[0].timestamp,
            "close_now": bars[-1].close,
            "close_30_ago": bars[-31].close if len(bars) >= 31 else None,
            "bar_ts": bars[-1].timestamp,
        }

    # ----------------------------------------------------------------- restore
    def after_restore(self) -> Dict[str, Any]:
        """Rebuild every symbol's machine from its stored session bars.

        Legacy v1 state (SymbolVWAPState with `session_bars`) and older v2 state are both
        replayed through the evaluator, so no stored index is trusted. Signals emitted
        during the rebuild are counted (cooldown) but never returned as new orders.
        """
        report: Dict[str, Any] = {"rebuilt": {}, "dropped": [], "failed": [], "invalidated": []}
        old = dict(self.symbol_states)
        rebuilt: Dict[str, V2SymbolState] = {}
        p = self.params()
        for sym, prior in old.items():
            key = sym.upper()
            try:
                bars = getattr(prior, "bars", None)
                if bars is None:
                    bars = getattr(prior, "session_bars", None)
                if not bars:
                    report["dropped"].append(key)
                    continue
                admitted = int(getattr(prior, "admitted_today", 0) or 0)
                cooldown = int(getattr(prior, "cooldown_until_i", -1) or -1)
                fresh = V2SymbolState()
                n = 0
                for b in bars:
                    if not isinstance(b, BarEvent):
                        continue
                    evaluate_bar(fresh, b, p, emission_allowed=True, vix_stop_mult=float(self.vix_stop_multiplier or 1.0),
                                 strategy_id=self.strategy_id, tape=None, tick_gates=False)
                    n += 1
                # Durable limits come from the stored state, never from the hypothetical replay.
                fresh.admitted_today = admitted
                fresh.cooldown_until_i = max(fresh.cooldown_until_i, cooldown)
                if fresh.state != "IDLE":
                    # A setup in progress lost its tick evidence with the restart: discard it.
                    report["invalidated"].append({"symbol": key, "state": fresh.state})
                    _to_idle(fresh)
                rebuilt[key] = fresh
                report["rebuilt"][key] = {"bars": n, "state": fresh.state, "admitted_today": admitted}
            except Exception as exc:  # keep the day's budget closed for a symbol we cannot trust
                blocked = V2SymbolState(session_date=getattr(prior, "session_date", None))
                blocked.admitted_today = p.max_signals_per_day
                rebuilt[key] = blocked
                report["failed"].append({"symbol": key, "error": f"{type(exc).__name__}: {exc}"})
        self.symbol_states = rebuilt
        self.restored_from = "rebuilt_from_session_bars"
        self.version, self.policy_id = VERSION, POLICY_ID   # a checkpoint never carries the policy identity
        return report

    # ----------------------------------------------------------------- ui
    def to_dict(self) -> Dict[str, Any]:
        d = super().to_dict()
        # Identity comes from the code, never from a restored checkpoint's copy of these attributes.
        self.version, self.policy_id = VERSION, POLICY_ID
        d.update({
            "version": VERSION,
            "policy_id": POLICY_ID,
            "mode": self.mode,
            "excluded_symbols": list(self.excluded_symbols),
            "first_possible_signal": FIRST_POSSIBLE_SIGNAL,
            "last_evaluated_bar": self.last_evaluated_bar,
            "today_counts": dict(self.event_counts_today),
            "setups_today": int(self.event_counts_today.get("IMPULSE", 0)),
            "signals_emitted_today": int(self.event_counts_today.get("SIGNAL", 0)),
            "data_layers": self.data_layers(),
            "enforced_gates": list(self.enforced_gates),
            "addons_enforced": self.addons_enforced,
            "last_block_by_symbol": dict(self.last_block_by_symbol),
        })
        return d

    def data_layers(self) -> Dict[str, Any]:
        """What each required data layer is doing right now (card + /health)."""
        tape = TAPE
        th = tape.health() if tape is not None and hasattr(tape, "health") else None
        try:
            from backend.app.core.macro_calendar import macro_calendar
            macro_ok, macro_err = macro_calendar.loaded, macro_calendar.error
        except Exception as exc:  # pragma: no cover
            macro_ok, macro_err = False, str(exc)
        return {
            "required": self.require_tick_layers,
            "layer1_ticks": {"live": bool(th and th["trades_seen"] > 0), "trades_seen": th["trades_seen"] if th else 0},
            "layer2_book": {"live": bool(th and th["quotes_seen"] > 0), "quotes_seen": th["quotes_seen"] if th else 0,
                            "depth": "top_of_book_nbbo"},
            "layer3_timestamps": {"source": "exchange_nanoseconds", "velocity_window_s": self.params().tick_velocity_window_s,
                                  "raw_prints": th["raw_prints_total"] if th else 0},
            "layer4_macro": {"calendar_loaded": macro_ok, "error": macro_err, "regime": "spy_qqq_vwap_ema+vix"},
        }
