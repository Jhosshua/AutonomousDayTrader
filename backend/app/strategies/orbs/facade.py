"""OrbsFacade: the only API ADT's ORB controller (phases 2/3) calls into ORBStraddle's copied decision code.

Every method wraps the ORBStraddle code path named below (ORBStraddle @71b001f, file:line). Nothing here
places orders or talks to Alpaca's trading API; the only network is the AlpacaRelay data/metadata relay.

  prep(day)          scanner.prep / scanner._prepare (scanner.py:810-856): universe = top 250 by prior
                     close x volume (scanner.watchlist, scanner.py:369-412), prior closes, 14-TR ATR.
  scan(...)          "preview"/"primary": scanner.run (scanner.py:1089-1100), as app.py:283-322 calls it
                     (09:36 preview, 09:38 final). "secondary": scanner.run_secondary (scanner.py:1103-1120),
                     as app.py:359-373 calls it each minute 09:45-10:15. The secondary board excludes exactly
                     what ORBStraddle's run_secondary excludes (scanner.py:1109-1118): skip_symbols = symbols
                     currently open/supervised (core._superv_open) and executed_today = every symbol with an
                     execution today (core._todays("execution")).
                     THE CALLER MUST PASS THE DURABLE UNION OF EXECUTED-TODAY SYMBOLS (persisted by the
                     controller across restarts), including trades already closed; otherwise a symbol traded
                     and closed earlier can reappear on a secondary board and be picked again.
                     ok = app._require_scan_coverage (app.py:114-121): coverage >= MIN_SCAN_COVERAGE.
                     Every successful scan is remembered per wave (board_id) for decide().
  decide(...)        auditor.run_autopilot's adaptive branch (auditor.py:480-496): adaptive.reply_text
                     (adaptive.py:807-833) + core.validate (core.py:334-445). The primary board is read back
                     the way core.load_cards/_load_raw (core.py:92-99, 196-208) reads the frozen board (JSON
                     round trip, valid cards only, first card per symbol); a secondary board is passed as the
                     scanner returned it (app.py:369 candidate_cards=sec_cards). `occupied` is core.execute's
                     "already held by this account" refusal (core.py:2176-2180): the pick is dropped, never
                     replaced; executed_today picks are refused the same way. Before deciding, the board is
                     validated like ORBStraddle's snapshot path (core._commit_snapshot/_read_snapshot,
                     auditor._past_cutoff): scan ok, same day, same wave, coverage >= MIN_SCAN_COVERAGE,
                     board_id equal to the last successful scan of that wave, and now < AUTOPILOT_CUTOFF;
                     otherwise verdict "refused" with the reason. The auditor's once-a-day / already-judged /
                     slots gates (auditor.py:390-470) belong to the controller (phase 2), not here.
  recheck(card, now) core.execute's per-pick re-check (core.py:2193-2211): candle rule, flow rules 1/2, then a
                     fresh macro veto; call it again right before the POST (core.py:2390-2400).
  macro_veto(...)    flow.macro_refusals (flow.py:392-404) for one symbol at `now`.
  latest_trade(sym)  core._fresh_price (core.py:1828-1858): relay /v2/stocks/{sym}/trades/latest?feed=sip,
                     print age must be within -5..+60 s.
  absorption_poll()  flow.ABSORPTION (flow.py:534-584) the way core._supervise uses it (core.py:3561-3580):
                     never blocks; starts/refreshes a background read and returns the latest FRESH result.

The copied modules are module singletons (scanner session state, relay client, lockout latch), so one
process holds one facade; a new OrbsFacade re-points them (fresh scan session, relay client and lockout latch).

Import-time limits: scanner.py and market.py read their ORBS_* tunables (page size, scan deadline, incremental
mode, repair windows, relay cache/body limits) from config.SCANNER_ENV once, at import. OrbsFacade re-evaluates
each of those module lines (their own source text) against the manifest's `scanner_env` and compares the result
with the constant the module actually holds; any mismatch is rejected (ValueError), never silently ignored. To
run with other tunables, apply the manifest with config.apply_manifest() before scanner/market are first
imported (the replay harness does this).
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import threading
from datetime import date, datetime
from typing import Iterable, Optional, Set, Tuple

from . import adaptive, config, flow, market, scanner, shim, signals
from .ticks import ET

log = logging.getLogger("adt.orbs")
# Module constants computed at import from config.getenv (see the module docstring).
_IMPORT_TIME_TUNABLES = {
    scanner: ("RELAY_TOKEN", "PAGE_LIMIT", "PAGE_ATTEMPTS", "SCAN_DEADLINE_S", "ORBS_INCREMENTAL",
              "CORRECTION_HORIZON_S", "REPAIR_WINDOW_S", "RECONCILE_INTERVAL_S"),
    market: ("MAX_CACHE_ENTRIES", "MAX_CACHE_BYTES", "MAX_RESPONSE_BYTES"),
}


class _ManifestEnv:
    """config.getenv semantics over a manifest's scanner_env (pinned null = the caller's default)."""

    def __init__(self, env):
        self.env = env

    def getenv(self, name, default=None):
        if name not in self.env:
            raise KeyError(f"{name} is not pinned in the manifest scanner_env")
        value = self.env[name]
        return default if value is None else value


def import_time_mismatches(manifest: dict) -> list:
    """[(module.NAME, module value, value the manifest implies)] for every import-time tunable that differs.
    The expected value comes from executing the module's OWN assignment line against the manifest env, so
    the formula is never duplicated here."""
    env = {k: v for k, v in manifest.get("scanner_env", {}).items() if not k.startswith("_")}
    out = []
    for module, names in _IMPORT_TIME_TUNABLES.items():
        lines = {ln.split(" = ", 1)[0]: ln for ln in inspect.getsource(module).splitlines() if " = " in ln}
        for name in names:
            ns = {"config": _ManifestEnv(env), "max": max, "min": min, "int": int, "float": float, "str": str}
            exec(lines[name], ns)            # e.g. PAGE_LIMIT = max(100, min(10000, int(config.getenv(...))))
            actual = getattr(module, name)
            if actual != ns[name]:
                out.append((f"{module.__name__.rsplit('.', 1)[1]}.{name}", actual, ns[name]))
    return out

WAVES = ("preview", "primary", "secondary")


def _require_scan_coverage(health: dict) -> Tuple[bool, Optional[str]]:
    """ORBStraddle app._require_scan_coverage (app.py:114-121) as (ok, why) instead of raising."""
    att, ok, failed = (health.get(k) for k in ("attempted", "ok", "failed"))
    if any(type(v) is not int for v in (att, ok, failed)) or att <= 0 or not 0 <= ok <= att or failed != att - ok:
        return False, "scan counts are invalid or inconsistent"
    if ok / att < config.MIN_SCAN_COVERAGE:
        return False, f"scan covered only {ok / att:.0%} of {att} symbols (floor {config.MIN_SCAN_COVERAGE:.0%})"
    return True, None


def frozen_board(cards: Iterable[dict]) -> list:
    """core._load_raw semantics for the primary board: JSON round trip (the board is read back from its
    file), only core._valid_card cards, first card per symbol, in board order."""
    seen, out = set(), []
    for c in json.loads(json.dumps(list(cards))):
        if not isinstance(c, dict) or not shim._valid_card(c) or c["symbol"] in seen:
            continue
        seen.add(c["symbol"])
        out.append(c)
    return out


def board_id(day: date, wave: str, end_hhmm: str, cards: list) -> str:
    blob = json.dumps({"day": day.isoformat(), "wave": wave, "end": end_hhmm, "cards": cards},
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


class OrbsFacade:
    def __init__(self, state_dir: str, relay_base: str, relay_token: str, manifest: Optional[dict] = None,
                 http=None):
        """state_dir: where the copied scanner writes its board files and receipts (never the repo).
        relay_base / relay_token: ADT settings RELAY_HTTP_URL / RELAY_TOKEN. manifest: PARITY_MANIFEST.json
        by default. http: optional urlopen-compatible transport (replay/record); default urllib."""
        manifest = manifest if manifest is not None else config.load_manifest()
        mismatches = import_time_mismatches(manifest)
        if mismatches:
            raise ValueError(f"manifest scanner_env does not match the loaded module constants {mismatches}; "
                             "apply it with config.apply_manifest() before scanner/market are imported")
        config.apply_manifest(manifest)
        config.set_relay(relay_base, relay_token)
        os.makedirs(state_dir, exist_ok=True)
        config.set_state_dir(state_dir)
        shim.set_transport(http)
        shim.set_occupancy()
        scanner.STATE = config.STATE
        # A brand-new latch: nothing from a previous state dir (locked flag, symbol, session) survives.
        signals.SESSION_LOCKOUT = signals.SessionLockoutManager(
            filepath=os.path.join(config.STATE, "session_lockout.json"))
        scanner._RELAY_CLIENT = market.RelayClient(base_url=config.RELAY_DATA, meta_url=config.RELAY_META,
                                                   token=config.RELAY_TOKEN, ua=config.RELAY_UA)
        scanner._SESSION = None
        scanner._SESSION_KEY = None
        scanner._prep.update(day=None, wl=None, prev_dv=None, atr={})
        scanner.adv_close = {}
        self.state_dir = config.STATE
        self._scan_lock = threading.Lock()
        self._abs_latest = {}
        self._abs_lock = threading.Lock()
        self._last_scan = {}          # wave -> {"board_id", "day", "end", "coverage"} of the last OK scan

    # ---------------- configuration ----------------
    def set_exclude_symbols(self, symbols: Iterable[str]) -> None:
        """ADT-only: names that stay on the board but can never be picked (audit reason excluded_symbol)."""
        config.set_exclude_symbols(symbols)

    def effective_config(self) -> dict:
        out = config.effective()
        out["EXCLUDE_SYMBOLS"] = sorted(config.EXCLUDE_SYMBOLS)
        out["scanner"] = {"PAGE_LIMIT": scanner.PAGE_LIMIT, "PAGE_ATTEMPTS": scanner.PAGE_ATTEMPTS,
                          "SCAN_DEADLINE_S": scanner.SCAN_DEADLINE_S, "ORBS_INCREMENTAL": scanner.ORBS_INCREMENTAL,
                          "CORRECTION_HORIZON_S": scanner.CORRECTION_HORIZON_S,
                          "REPAIR_WINDOW_S": scanner.REPAIR_WINDOW_S,
                          "RECONCILE_INTERVAL_S": scanner.RECONCILE_INTERVAL_S}
        return out

    # ---------------- board ----------------
    def prep(self, day: date) -> dict:
        try:
            scanner.prep(day)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "universe_size": 0}
        wl = list(scanner._prep.get("wl") or [])
        if scanner._prep.get("day") != day or not wl:
            return {"ok": False, "error": "no prior-session reference data", "universe_size": len(wl)}
        return {"ok": True, "error": None, "universe_size": len(wl), "universe": wl,
                "closes": len(scanner.adv_close), "atr": len(scanner._prep.get("atr") or {}),
                "reference_session": scanner.REFERENCE_SESSION_DATE}

    def scan(self, day: date, end: datetime, wave: str, skip_symbols: Set[str],
             executed_today: Optional[Set[str]] = None) -> dict:
        if wave not in WAVES:
            raise ValueError(f"unknown wave {wave!r}")
        if end.tzinfo is None:
            raise ValueError("scan end must be timezone-aware")
        end_et = end.astimezone(ET)
        if end_et.date() != day or end_et.second or end_et.microsecond:
            raise ValueError("scan end must be a whole minute on the scan day (ORBStraddle cuts at HH:MM)")
        end_hhmm = end_et.strftime("%H:%M")
        skip = {str(s).upper() for s in (skip_symbols or ())}
        if wave == "secondary" and executed_today is None:
            raise ValueError("a secondary scan needs executed_today (the durable set of symbols executed today)")
        executed = {str(s).upper() for s in (executed_today or ())}
        if wave != "secondary" and (skip or executed):
            raise ValueError("preview/primary boards never skip symbols (ORBStraddle scanner.run); "
                             "apply occupancy in decide()")
        with self._scan_lock, shim.frozen_clock(end_et):
            try:
                if wave == "secondary":
                    shim.set_occupancy(supervised=skip, executed=executed)
                    try:
                        cards, health = scanner.run_secondary(day, end_hhmm)
                    finally:
                        shim.set_occupancy()
                else:
                    cards, health = scanner.run(day, end_hhmm)
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "coverage": None,
                        "cards": [], "board_id": None, "wave": wave, "end": end_hhmm, "health": None}
        if wave == "secondary" and not health.get("attempted"):
            ok, why = True, None            # app.py:370 only checks coverage when symbols were attempted
        else:
            ok, why = _require_scan_coverage(health)
        bid = board_id(day, wave, end_hhmm, cards)
        if ok:
            self._last_scan[wave] = {"board_id": bid, "day": day.isoformat(), "end": end_hhmm,
                                     "coverage": health.get("coverage")}
        return {"ok": ok, "error": why, "coverage": health.get("coverage"), "cards": cards,
                "board_id": bid, "wave": wave, "end": end_hhmm, "health": health}

    def _board_refusal(self, day: date, board, wave: str, now_et: datetime) -> Optional[str]:
        """Why this board may not be decided (ORBStraddle's snapshot/cutoff checks), or None."""
        if not isinstance(board, dict):
            return "board is not a scan result"
        if board.get("ok") is not True:
            return f"the scan did not succeed ({board.get('error')})"
        if board.get("wave") != wave:
            return f"board is from the {board.get('wave')} wave, not {wave}"
        if not board.get("cards"):
            return "the board is empty"          # core._commit_snapshot / app.py:369 never decide an empty board
        health = board.get("health") or {}
        if health.get("day") != day.isoformat():
            return f"board is for {health.get('day')}, not {day.isoformat()}"
        if wave == "primary" or health.get("attempted"):
            ok, why = _require_scan_coverage(health)
            if not ok:
                return why
        if wave == "primary":
            # core._commit_snapshot (core.py:118-146): only the FINAL board (end == FREEZE_ET, the active card
            # source), decided at/after the freeze, and every card of it valid (count == scan metadata).
            if health.get("end") != config.FREEZE_ET or board.get("end") != config.FREEZE_ET:
                return f"board is the {health.get('end')} board, not the {config.FREEZE_ET} final"
            if health.get("source") != config.CARD_SOURCE:
                return "scan source does not match the active card source"
            fh, fm = map(int, config.FREEZE_ET.split(":"))
            if (now_et.hour, now_et.minute) < (fh, fm):
                return f"decision time is before the {config.FREEZE_ET} freeze"
            valid = frozen_board(board.get("cards") or [])
            if not valid:
                return "no valid cards in the final scan"
            if type(health.get("cards")) is not int or len(valid) != health["cards"]:
                return "card count differs from scan metadata"
        last = self._last_scan.get(wave)
        if last is None or last["day"] != day.isoformat() or last["board_id"] != board.get("board_id"):
            return f"board is not the last successful {wave} scan"
        if board.get("board_id") != board_id(day, wave, str(board.get("end")), board.get("cards") or []):
            return "board cards do not match their board_id"
        h, m = map(int, config.AUTOPILOT_CUTOFF.split(":"))
        if (now_et.hour, now_et.minute) >= (h, m):
            return f"past the {config.AUTOPILOT_CUTOFF} cutoff"
        return None

    # ---------------- decision ----------------
    def decide(self, day: date, board: dict, wave: str, now: datetime, occupied: Set[str],
               executed_today: Optional[Set[str]] = None) -> dict:
        if wave not in ("primary", "secondary"):
            raise ValueError("only the primary and secondary boards are decided")
        if now.tzinfo is None:
            raise ValueError("decision time must be timezone-aware")
        now_et = now.astimezone(ET)
        if now_et.date() != day:
            raise ValueError("decision time must be on the board's day")
        occupied = {str(s).upper() for s in (occupied or ())}
        executed = {str(s).upper() for s in (executed_today or ())}
        refusal = self._board_refusal(day, board, wave, now_et)
        if refusal:
            return {"verdict": "refused", "reason": refusal, "picks": [], "refused": [], "audit": [],
                    "regime": {}, "reply": None, "note": None, "cards": [], "wave": wave,
                    "now": now_et.isoformat(), "valid": False, "validated": None}
        raw = board.get("cards")
        cards = frozen_board(raw or []) if wave == "primary" else list(raw or [])
        with shim.frozen_clock(now_et):
            text, note = adaptive.reply_text(cards, day.isoformat())
            good, res = shim.validate(text, cards)
        reply = json.loads(text)
        regime = reply.get("regime") or {}
        audit = reply.get("audit") or []
        out = {"verdict": None, "reason": None, "picks": [], "refused": [], "audit": audit, "regime": regime,
               "reply": reply, "note": note, "cards": cards, "wave": wave, "now": now_et.isoformat(),
               "valid": good, "validated": res}
        if not good:
            out.update(verdict="rejected", reason=f"adaptive reply rejected by the validator: {res}")
            return out
        if not res:
            sat_out = regime.get("action") == "SIT_OUT_CASH"
            out.update(verdict="sit_out" if sat_out else "pass", reason=note)
            return out
        by_sym = {c["symbol"]: c for c in cards}
        reply_picks = {p.get("symbol"): p for p in reply.get("picks") or []}
        for p in res:
            sym = p["symbol"]
            if sym in occupied:
                out["refused"].append({"symbol": sym, "reason": "already held by this account"})
                continue
            if sym in executed:
                out["refused"].append({"symbol": sym, "reason": "already executed today"})
                continue
            card = by_sym[sym]
            out["picks"].append({
                **p,
                "side": "buy" if p["direction"] == "long" else "sell",
                "entry": card["entry"],
                "stop": card["stop"],
                "mode": card.get("mode"),
                "catalyst_summary": (reply_picks.get(sym) or {}).get("catalyst_summary"),
                "card": card,
            })
        out["verdict"] = "trade" if out["picks"] else "blocked"
        out["reason"] = None if out["picks"] else "every pick is already held or already executed today"
        return out

    def recheck(self, card: dict, now: datetime) -> Tuple[bool, str]:
        pick = {"symbol": card.get("symbol"), "direction": card.get("direction")}
        if config.CANDLE_RULE:
            why = signals.candle_refusal(card, pick)
            if why:
                return False, "candle rule: " + why
        why = flow.flow_refusal(card, pick)
        if why:
            return False, "flow rule: " + why
        if config.MACRO_RULE:
            why = flow.macro_refusals([pick], now=now).get(pick["symbol"], "no macro verdict")
            if why:
                return False, "macro veto: " + why
        return True, ""

    def macro_veto(self, symbol: str, direction: str, now: datetime) -> Tuple[bool, str]:
        """(vetoed, reason). vetoed=True means the trade must NOT be placed (fail-closed on missing data)."""
        why = flow.macro_refusals([{"symbol": symbol, "direction": direction}], now=now).get(
            symbol, "no macro verdict")
        return (True, why) if why else (False, "")

    # ---------------- execution-time reads ----------------
    def latest_trade(self, symbol: str) -> Optional[dict]:
        now = shim.now_et()
        try:
            px, stamp = shim._fresh_price(symbol, now=now)
        except Exception as exc:
            log.info("orbs latest_trade %s refused: %s", symbol, exc)
            return None
        return {"price": px, "ts": stamp, "age_s": (now - stamp.astimezone(ET)).total_seconds()}

    def absorption_poll(self, symbol: str, direction: str, now: datetime) -> Optional[dict]:
        now_et = now.astimezone(ET)
        with self._abs_lock:
            self._abs_latest.update(flow.ABSORPTION.take_fresh(now_et))
            flow.ABSORPTION.request(symbol, direction, now_et)
            res = self._abs_latest.get(symbol)
            if res and res.get("direction") == direction and flow.fresh_read(res, now_et):
                return self._abs_latest.pop(symbol)
            if res and not flow.fresh_read(res, now_et):
                self._abs_latest.pop(symbol, None)
        return None
