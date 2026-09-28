"""ADT stand-in for the ORBStraddle `core.py` functions that the copied decision modules call.

ORBStraddle's core.py (4.4k lines) is its execution engine, bound to its own files, ledger and broker. The
decision modules only need a handful of its functions; this shim provides exactly those:

  now_et / today      clock (ORBStraddle core.py:65-66). `frozen_clock(now)` pins it for one call
                      (context-local, used by the facade's decide() and by replay).
  _valid_card, validate, _sanitize, _json_objects, _extract_json, _num
                      VERBATIM copies of core.py @71b001f (lines 80-92, 287-445, 3648-3652); the sync test
                      compares their source text with ORBStraddle's.
  long_only           core.py:1788 for the only modes ADT supports ("" / "no" = never, "yes" = always).
  _superv_open, _todays("execution")
                      ORBStraddle's "already supervised / executed today" reads, answered from the
                      occupancy ADT injects (set_occupancy) instead of supervision.json / the ledger.
  status_heartbeat, _ledger
                      telemetry only: no files; ledger rows go to the logger and a bounded in-memory list.
  urlopen             the HTTP transport every copied relay call goes through (default
                      urllib.request.urlopen; set_transport() injects a replay/record transport).
  _fresh_price        core.py:1828-1858 adapted to ADT's relay settings (returns price and print time).
"""
from __future__ import annotations

import collections
import contextlib
import contextvars
import json
import logging
import math
import re
import threading
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from . import config
from .ticks import _ts as parse_market_timestamp

ET = ZoneInfo("America/New_York")
log = logging.getLogger("adt.orbs")

# ---------------- clock ----------------
_NOW_OVERRIDE: contextvars.ContextVar = contextvars.ContextVar("orbs_now_override", default=None)


def now_et():
    pinned = _NOW_OVERRIDE.get()
    return pinned if pinned is not None else datetime.now(ET)


def today():
    return now_et().strftime("%Y-%m-%d")


@contextlib.contextmanager
def frozen_clock(now: Optional[datetime]):
    """Pin now_et()/today() for the current context (thread / task). None = real clock."""
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ValueError("frozen_clock needs a timezone-aware datetime")
    token = _NOW_OVERRIDE.set(now.astimezone(ET) if now is not None else None)
    try:
        yield
    finally:
        _NOW_OVERRIDE.reset(token)


# ---------------- HTTP transport ----------------
_TRANSPORT: Callable = urllib.request.urlopen


def set_transport(fn: Optional[Callable]) -> None:
    """fn(request, timeout=...) -> context-manager response (urlopen contract). None restores urllib."""
    global _TRANSPORT
    _TRANSPORT = fn or urllib.request.urlopen


def urlopen(req, timeout=None):
    return _TRANSPORT(req, timeout=timeout)


# ---------------- occupancy (ORBStraddle supervision.json / ledger "execution" rows) ----------------
_OCC_LOCK = threading.Lock()
_OCCUPANCY = {"supervised": frozenset(), "executed": frozenset()}


def set_occupancy(supervised: Iterable[str] = (), executed: Iterable[str] = ()) -> None:
    with _OCC_LOCK:
        _OCCUPANCY["supervised"] = frozenset(str(s).upper() for s in supervised or ())
        _OCCUPANCY["executed"] = frozenset(str(s).upper() for s in executed or ())


def _superv_open():
    with _OCC_LOCK:
        return {sym: {"symbol": sym} for sym in sorted(_OCCUPANCY["supervised"])}


def _todays(kind):
    with _OCC_LOCK:
        executed = sorted(_OCCUPANCY["executed"])
    if kind == "execution" and executed:
        return [{"kind": "execution", "day": today(), "picks": [{"symbol": s} for s in executed]}]
    return []


# ---------------- telemetry ----------------
LEDGER_ROWS: collections.deque = collections.deque(maxlen=500)


def status_heartbeat():
    return None


def _ledger(row):
    row["ts"] = now_et().isoformat()
    row.setdefault("rules_version", config.RULES_VERSION)
    LEDGER_ROWS.append(dict(row))
    log.info("orbs ledger %s", json.dumps(row, default=str)[:2000])


# ---------------- account mode ----------------
def long_only(acct=None):
    return str(getattr(config, "LONG_ONLY_MODE", "")).strip().lower() == "yes"


# ---------------- execution price (core.py:1828-1858, adapted) ----------------
def _fresh_price(sym, now=None):
    """Latest SIP print from the relay, refused unless its age is within -5..+60 s of now.
    Returns (price, print_time). Raises RuntimeError on any problem (ORBStraddle's contract)."""
    url = config.RELAY_DATA
    req = urllib.request.Request(url.rstrip("/") + f"/v2/stocks/{urllib.parse.quote(sym)}/trades/latest?feed=sip",
                                 headers={"X-Relay-Token": config.RELAY_TOKEN})
    with urlopen(req, timeout=8) as r:
        j = json.loads(r.read())
    tr = j.get("trade") or {}
    px = float(tr.get("p") or 0)
    if not math.isfinite(px) or px <= 0: raise RuntimeError("no usable last price")
    try:
        stamp = parse_market_timestamp(tr.get("t") or "")
        if stamp.tzinfo is None:
            raise ValueError("timezone missing")
        age = ((now or now_et()) - stamp.astimezone(ET)).total_seconds()
    except (TypeError, ValueError) as exc:
        raise RuntimeError("last price has no valid timestamp") from exc
    if not -5 <= age <= 60:
        raise RuntimeError(f"last print age {age:.1f}s is stale or in the future")
    return px, stamp


# ---------------- VERBATIM from ORBStraddle core.py @71b001f (do not edit; test_sync compares) ----------------
def _valid_card(c):
    try:
        if c.get("direction") not in ("long", "short"): return False
        if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,6}", str(c.get("symbol", ""))): return False
        e, s = float(c["entry"]), float(c["stop"])
        if not (math.isfinite(e) and math.isfinite(s) and e > 0 and s > 0 and e != s): return False
        if c["direction"] == "long" and s >= e: return False
        if c["direction"] == "short" and s <= e: return False
        for k in ("gap_pct", "drift_pct", "rvol", "atr_pct", "spread_bps"):
            if c.get(k) is not None and not math.isfinite(float(c[k])): return False
        return True
    except Exception: return False

def _sanitize(s, cap=200):
    s = re.sub(r"[\x00-\x1f\x7f]", "", str(s)); s = re.sub(r"https?://\S+|[@#`*_\[\]<>]", "", s)
    return s[:cap]

def _json_objects(t):
    r"""Every top-level {...} span in the text, brace-balanced and string-aware, in order.
    A greedy \{.*\} match spans TWO objects when the model prints a draft and then a corrected
    block, and the whole paste is then rejected as malformed. Seen from agy 2026-08-31."""
    out, depth, start, instr, esc = [], 0, None, False, False
    for i, ch in enumerate(t):
        if instr:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch == '"': instr = False
            continue
        if ch == '"': instr = True
        elif ch == "{":
            if depth == 0: start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(t[start:i + 1]); start = None
    return out

def _extract_json(reply_text):
    """Pull the reply's JSON out of whatever the terminal did to it on the way through.
    Tolerates: ```json fences, CRLF, leading/trailing blank lines and indentation, hard-wrapped
    lines with raw newlines inside strings, non-breaking spaces from a browser copy, smart quotes
    around the whole block, and a draft object followed by a corrected one (the LAST wins).
    Returns (obj, None) or (None, reason)."""
    t = reply_text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\u00a0", " ").replace("\u2028", "\n").replace("\u2029", "\n")
    t = re.sub(r"```[a-zA-Z]*", "", t)                 # fences anywhere, not just at the ends
    objs = _json_objects(t)
    if not objs: return None, "no JSON object found in the reply"
    err = "no JSON object found in the reply"
    for raw in reversed(objs):                          # last complete object wins
        for attempt in (raw,
                        re.sub(r"[ \t]*\n[ \t]*", " ", raw),        # unwrap terminal line breaks
                        re.sub(r",(\s*[}\]])", r"\1", re.sub(r"[ \t]*\n[ \t]*", " ", raw))):  # + trailing commas
            try:
                j = json.loads(attempt, strict=False)   # strict=False: raw newlines inside strings
                if isinstance(j, dict) and "picks" in j: return j, None
            except Exception as e:
                err = f"JSON parse failed even after unwrap: {e}"
    return None, err

def validate(reply_text, cards):
    if not isinstance(reply_text, str) or len(reply_text) > 200_000: return False, "reply missing or too large"
    j, perr = _extract_json(reply_text)
    if j is None: return False, perr
    if not isinstance(j, dict): return False, "reply is not a JSON object"
    if j.get("date") != today(): return False, f"date mismatch: reply says {j.get('date')!r}, today is {today()} (stale paste?)"
    picks = j.get("picks", [])
    if not isinstance(picks, list) or any(not isinstance(p, dict) for p in picks): return False, "picks malformed"
    if not isinstance(j.get("pass"), bool) or j["pass"] != (len(picks) == 0): return False, "pass flag inconsistent with picks"
    if len(picks) > config.MAX_PICKS: return False, f"more than {config.MAX_PICKS} picks"
    by_sym = {c["symbol"]: c for c in cards}
    # The judgment tier has no numeric gate of its own, so the model's OWN verdict is the evidence and the
    # desk re-checks it here. Under it6 this slot held the quiet-tier numeric re-check; dropping it without
    # a replacement would have let a pick the model itself withdrew reach a live order (codex P1, 08-31).
    audit = {}
    if not isinstance(j.get("audit", []), list): return False, "audit malformed"
    for a in (j.get("audit") or []):
        if isinstance(a, dict) and isinstance(a.get("symbol"), str):
            audit[a["symbol"].upper().strip()] = a
    out = []; structure = 0; seen = set()
    for p in picks:
        sym = p.get("symbol"); tier = p.get("tier", "earnings")
        if not isinstance(sym, str) or sym not in by_sym:
            return False, f"unknown symbol {sym!r} (not in the frozen card list)"
        if sym in seen: return False, f"duplicate symbol {sym}"
        card = by_sym[sym]
        if not _valid_card(card) or any(card.get(k) is None or isinstance(card.get(k), bool)
                for k in ("gap_pct", "drift_pct", "rvol", "atr_pct", "spread_bps")):
            return False, f"{sym} has missing or invalid card inputs"
        seen.add(sym)
        if p.get("direction") != by_sym[sym]["direction"]: return False, f"direction mismatch on {sym}"
        if tier not in ("earnings", "structure"): return False, f"unknown tier {tier!r} on {sym}"
        # accept any of the reason field names the model has used; the field NAME is not the point,
        # the written reason is. Rejecting a good pick over a key spelling costs a real trade.
        reason = _sanitize(next((p[k] for k in ("catalyst_summary", "why", "reason", "case_for")
                                 if isinstance(p.get(k), str) and p[k].strip()), ""))
        if not reason.strip():
            # the judgment tier has no numeric gate to re-check server-side, so the stated reason IS the
            # evidence: a pick with nothing written against its name is not a decision, it is a guess.
            return False, f"{tier} pick {sym} has no catalyst_summary (evidence required)"
        if tier == "structure": structure += 1
        # --- the pick must agree with its own audit row ---
        row = audit.get(sym)
        if row is None:
            return False, f"{tier} pick {sym} has no audit row (the desk trades evidence, not a bare symbol)"
        # These used to reject only one exact bad value, so a MISSING field, the string "false", or any
        # unexpected type sailed through. With a single model call and no second opinion, that is the
        # whole safety layer failing open (codex, 2026-08-31). Require the affirmative value, exactly.
        if row.get("survives") is not True:
            return False, f"{sym} is picked but its audit row does not say survives=true (got {row.get('survives')!r})"
        if str(row.get("case_against_strength", "")).strip().lower() != "weak":
            return False, (f"{sym} is picked but its own case against it is not marked 'weak' "
                           f"(got {row.get('case_against_strength')!r} — the prompt says withdraw)")
        row_tier = str(row.get("tier") or row.get("kind") or tier).strip().lower()
        if row_tier != tier:
            return False, f"{sym}: the pick says tier {tier!r} but its audit row says {row_tier!r}"
        if tier == "earnings":
            # the earnings tier is the one place with fixed numbers, so re-check them rather than trust prose
            if row.get("earnings_found") is not True:
                return False, f"earnings pick {sym} does not assert earnings_found=true"
            if row.get("direction_aligned") is not True:
                # The one legitimate exception: a deliberate fade of a big gap. Measured on the
                # tuned window these are all beats that gapped UP and are taken short, i.e. fading
                # an overreaction to good news. `against` is measured against the TRADE direction.
                # It must be declared, and the size is re-derived from the FROZEN CARD - asserting
                # reprice is not enough, the card has to actually show the gap.
                if row.get("reprice") is not True:
                    return False, f"earnings pick {sym} does not assert direction_aligned=true"
                g = _num(by_sym[sym].get("gap_pct"))
                # _num returns 0.0 for junk, so test the raw value too: a NaN satisfies every
                # comparison (nan < 3.0 is False), which would turn this guard into a pass. The
                # hours_ago check five lines below already does this; the gap check did not.
                raw = by_sym[sym].get("gap_pct")
                try: fg = float(raw)
                except (TypeError, ValueError): fg = None
                if raw is None or fg is None or not (fg == fg) or fg in (float("inf"), float("-inf")):
                    return False, f"reprice pick {sym} has no usable gap on the card to justify it"
                against = -fg * (1 if by_sym[sym]["direction"] == "long" else -1)
                if against < config.EARN_REPRICE_GAP:
                    return False, (f"reprice pick {sym} needs a gap of at least "
                                   f"{config.EARN_REPRICE_GAP}% against its direction, card shows {against:+.1f}%")
            try: hrs = float(row.get("hours_ago"))
            except (TypeError, ValueError): return False, f"earnings pick {sym} has no numeric hours_ago"
            if not (hrs == hrs) or hrs in (float("inf"), float("-inf")):
                return False, f"earnings pick {sym} has a nonsense hours_ago ({hrs})"
            if not (0.0 <= hrs <= 48.0):
                return False, f"earnings pick {sym} is {hrs}h old (must be between 0.0 and 48.0)"
            # drift comes from the FROZEN CARD, not from the model's transcription of it
            drift = by_sym[sym].get("drift_pct")
            if drift is not None and drift <= -0.5:
                return False, f"earnings pick {sym} has card DRIFT_% {drift} (must be greater than -0.5)"
        rec = dict(symbol=sym, direction=p["direction"], tier=tier, reason=reason)
        if tier == "earnings" and row.get("reprice") is True:
            # carry the marker into execution_intent / execution / the closed-trade ledger so the
            # fade exception can be measured on its own later, instead of only living in the raw reply
            rec["reprice"] = True
        out.append(rec)
    if structure > config.MAX_STRUCTURE: return False, f"more than {config.MAX_STRUCTURE} structure picks"
    return True, out

def _num(v, d=0.0):
    try:
        value = float(v)
        return value if math.isfinite(value) else d
    except Exception: return d

