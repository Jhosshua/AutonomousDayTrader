"""Recorded-market relay for the ORB dry run (replay only, read-only, no network).

Answers every AlpacaRelay request ADT's ORB code makes, from files recorded earlier:

  base tape   the phase-1 parity cache (research/orbs_parity_cache/<date>/tape, 09:30-10:15, READ ONLY,
              by default the worktree that recorded it): raw SIP trades + quotes of the 250-name board.
  ext tape    a top-up for the few symbols ADT actually trades, 10:15-11:05 (topup.py, same row format).
  exact       the parity cache's exact request -> body store (calendar, assets, daily bars, the 09:38
              SPY/QQQ bars, news, sector bars); an exact hit is always preferred.
  bars        1-minute bars of SPY, QQQ and the 11 sector ETFs 09:30-11:05 (topup.py). A 1-minute bars
              request with no exact hit (macro veto at a time the parity run never asked) is answered by
              slicing these bars to [start, end]; validate() proves the slicer reproduces every recorded
              exact 1-minute bars answer.
  news        a news request with no exact hit (a board that differs from the parity run because ADT left
              out a symbol it already traded) is answered from the union of every recorded news article
              for the same [start, end] window, filtered to the requested symbols, newest first. Sound because
              every recorded news answer was fetched to its last page; validate() checks the reproduction.
  latest      /trades/latest = the last recorded SIP print at or before the simulated now.

Anything else is a MISS: recorded and raised (the run then fails loudly)."""
from __future__ import annotations

import bisect
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.parse
from array import array
from datetime import date, datetime, time as dtime, timezone
from typing import Callable, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "orbs_parity"))
import parity_common as pc  # noqa: E402

ET = pc.ET
BASE_CACHE = os.environ.get(
    "ORB_DRY_BASE_CACHE",
    "/Users/mo/AutonomousDayTrader/.claude/worktrees/agent-ac04709cd46b4178f/research/orbs_parity_cache")
TOPUP_ROOT = os.path.join(REPO, "research", "orbs_parity_cache", "dry_run_topup")   # gitignored
EXT_START, EXT_END = dtime(10, 15), dtime(11, 5)
ETFS = ["SPY", "QQQ", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY"]
LATEST_RE = re.compile(r"^/data/v2/stocks/([A-Z][A-Z0-9.]{0,9})/trades/latest$")


def base_dir(day: str) -> str:
    return os.path.join(BASE_CACHE, day)


def topup_dir(day: str) -> str:
    return os.path.join(TOPUP_ROOT, day)


def at_ns(day: str, t: dtime) -> int:
    return pc.ts_ns(datetime.combine(date.fromisoformat(day), t, tzinfo=ET).isoformat())


class _Tape:
    """One store directory of <SYM>.<kind>.jsonl + .ts (parity_common format)."""

    def __init__(self, folder: str, start_ns: int, end_ns: int):
        self.folder, self.start_ns, self.end_ns = folder, start_ns, end_ns
        self._cache: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()

    def paths(self, sym, kind):
        b = os.path.join(self.folder, f"{sym}.{kind}")
        return b + ".jsonl", b + ".ts"

    def has(self, sym, kind):
        a, b = self.paths(sym, kind)
        return os.path.exists(a) and os.path.exists(b)

    def load(self, sym, kind):
        key = (sym, kind)
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None:
            return hit
        rows_path, idx_path = self.paths(sym, kind)
        with open(rows_path, "rb") as f:
            lines = f.read().split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        stamps = array("q")
        with open(idx_path, "rb") as f:
            stamps.frombytes(f.read())
        if len(stamps) != len(lines):
            raise pc.ReplayMiss(f"tape index mismatch for {sym} {kind} in {self.folder}")
        with self._lock:
            self._cache[key] = (lines, stamps)
        return lines, stamps


class MergedTape:
    """Base tape (09:30-10:15) + extension (10:15-11:05) as one sorted row list per symbol and kind."""

    def __init__(self, day: str):
        self.day = day
        self.base = _Tape(os.path.join(base_dir(day), "tape"), at_ns(day, pc.TAPE_START), at_ns(day, pc.TAPE_END))
        self.ext = _Tape(os.path.join(topup_dir(day), "tape"), at_ns(day, EXT_START), at_ns(day, EXT_END))
        self._merged: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()

    def coverage_end(self, sym: str, kind: str) -> int:
        return self.ext.end_ns if self.ext.has(sym, kind) else self.base.end_ns

    def rows(self, sym: str, kind: str):
        key = (sym, kind)
        with self._lock:
            hit = self._merged.get(key)
        if hit is not None:
            return hit
        if not self.base.has(sym, kind):
            raise pc.ReplayMiss(f"no tape for {sym} {kind}")
        lines, stamps = self.base.load(sym, kind)
        if self.ext.has(sym, kind):
            el, es = self.ext.load(sym, kind)
            # the extension was fetched from 10:15:00 inclusive; the base already holds rows stamped exactly
            # 10:15:00.000 (end inclusive), so only rows after the base's end, or equal rows not already there
            tail_equal = set(lines[bisect.bisect_left(stamps, self.base.end_ns):])
            add_l, add_s = [], array("q")
            for ln, s in zip(el, es):
                if s > self.base.end_ns or (s == self.base.end_ns and ln not in tail_equal):
                    add_l.append(ln)
                    add_s.append(s)
            lines = list(lines) + add_l
            stamps = array("q", stamps)
            stamps.extend(add_s)
        out = (lines, stamps)
        with self._lock:
            self._merged[key] = out
        return out

    def serve(self, sym: str, kind: str, params: dict) -> bytes:
        if params.get("feed") != "sip" or params.get("sort", "asc") != "asc":
            raise pc.ReplayMiss(f"tape serves feed=sip sort=asc only: {params}")
        start, end = pc.ts_ns(params["start"]), pc.ts_ns(params["end"])
        if start < self.base.start_ns or end > self.coverage_end(sym, kind):
            raise pc.ReplayMiss(f"{sym} {kind} window {params['start']}..{params['end']} outside the recorded tape "
                                f"(top up {sym} with topup.py)")
        limit = int(params.get("limit", 10000))
        lines, stamps = self.rows(sym, kind)
        lo, hi = bisect.bisect_left(stamps, start), bisect.bisect_right(stamps, end)
        token = params.get("page_token")
        i = lo
        if token:
            m = re.fullmatch(r"replay:(\d+)", token)
            if not m:
                raise pc.ReplayMiss(f"foreign page token {token!r}")
            i = int(m.group(1))
        j = min(i + limit, hi)
        nxt = (b'"replay:%d"' % j) if j < hi else b"null"
        return (b'{"' + kind.encode() + b'":[' + b",".join(lines[i:j]) + b'],"symbol":"' + sym.encode()
                + b'","next_page_token":' + nxt + b"}")

    def last_trade(self, sym: str, now_ns: int) -> Optional[dict]:
        if now_ns > self.coverage_end(sym, "trades"):
            raise pc.ReplayMiss(f"latest trade for {sym} at {now_ns} is past the recorded tape (top up {sym})")
        lines, stamps = self.rows(sym, "trades")
        k = bisect.bisect_right(stamps, now_ns)
        return json.loads(lines[k - 1]) if k else None

    def prints(self, sym: str):
        """(stamps array, prices list, sizes list, odd-lot flags) for the broker simulator."""
        lines, stamps = self.rows(sym, "trades")
        px, sz, odd = [], [], []
        for ln in lines:
            r = json.loads(ln)
            px.append(float(r["p"]))
            sz.append(int(r.get("s") or 0))
            odd.append("I" in (r.get("c") or ()))
        return stamps, px, sz, odd


class BarStore:
    """1-minute bars recorded once (topup.py) and sliced like the relay: bars whose start is in [start, end]."""

    def __init__(self, day: str):
        self.path = os.path.join(topup_dir(day), "bars_1min.json")
        self.bars: Dict[str, list] = {}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.bars = json.load(f)["bars"]

    def ok(self) -> bool:
        return bool(self.bars)

    def serve(self, params: dict) -> bytes:
        if params.get("timeframe") != "1Min" or params.get("feed") != "sip" or params.get("page_token"):
            raise pc.ReplayMiss(f"bar store serves 1Min sip first pages only: {params}")
        start, end = pc.ts_ns(params["start"]), pc.ts_ns(params["end"])
        out = {}
        for sym in params["symbols"].split(","):
            if sym not in self.bars:
                raise pc.ReplayMiss(f"bar store has no {sym}")
            rows = [b for b in self.bars[sym] if start <= pc.ts_ns(b["t"]) <= end]
            if rows:
                out[sym] = rows
        if sum(len(v) for v in out.values()) > int(params.get("limit", 1000)):
            raise pc.ReplayMiss("bar store answer would need a second page")
        return json.dumps({"bars": out, "next_page_token": None}, separators=(",", ":")).encode()


class NewsStore:
    """Union of every recorded news article per [start, end] window, answered for any symbol subset."""

    def __init__(self, exact: "pc.ExactStore"):
        self.windows: Dict[tuple, Dict[int, dict]] = {}
        self.symbols_seen: Dict[tuple, set] = {}
        for key, row in exact._index.items():
            path, q = key.split("?", 1)
            if not path.endswith("/v1beta1/news") or row["status"] != 200:
                continue
            qd = dict(urllib.parse.parse_qsl(q, keep_blank_values=True))
            win = (qd["start"], qd["end"], qd.get("include_content"), qd.get("sort"))
            with open(os.path.join(exact.dir, row["file"]), "rb") as f:
                body = json.loads(f.read())
            store = self.windows.setdefault(win, {})
            for a in body.get("news") or []:
                store[a["id"]] = a
            self.symbols_seen.setdefault(win, set()).update(qd["symbols"].split(","))

    def serve(self, params: dict) -> bytes:
        win = (params["start"], params["end"], params.get("include_content"), params.get("sort"))
        if win not in self.windows or params.get("sort") != "desc" or params.get("page_token"):
            raise pc.ReplayMiss(f"news window {win} not recorded (or paged)")
        want = set(params["symbols"].split(","))
        unseen = want - self.symbols_seen[win]
        if unseen:
            raise pc.ReplayMiss(f"news for {sorted(unseen)} was never recorded for {win}")
        rows = [a for a in self.windows[win].values() if want & set(a.get("symbols") or [])]
        rows.sort(key=lambda a: (a.get("created_at") or "", a["id"]), reverse=True)
        # one page holding every match (the client follows next_page_token; there is none)
        return json.dumps({"news": rows, "next_page_token": None}, separators=(",", ":")).encode()


class DryRunTransport:
    """urlopen(request, timeout=...) stand-in for every relay call (shim.urlopen and scanner). Read-only."""

    def __init__(self, day: str, now_ns: Callable[[], int], latency_s: float = 0.0):
        self.day = day
        self.latency_s = latency_s
        self.now_ns = now_ns
        self.tape = MergedTape(day)
        self.exact = pc.ExactStore.__new__(pc.ExactStore)
        self.exact.dir = os.path.join(base_dir(day), "exact")
        self.exact.index_path = os.path.join(self.exact.dir, "index.jsonl")
        self.exact._index = {}
        self.exact._lock = threading.Lock()
        with open(self.exact.index_path) as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    self.exact._index[row["key"]] = row
        self.bars = BarStore(day)
        self.news = NewsStore(self.exact)
        self.misses: List[str] = []
        self.counts = {"tape": 0, "exact": 0, "bars_synth": 0, "news_synth": 0, "latest": 0}
        self.log: List[tuple] = []          # (kind, path, sim_ns) for non-tape requests
        self._lock = threading.Lock()

    def _count(self, k):
        with self._lock:
            self.counts[k] += 1

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if method != "GET":
            raise PermissionError("dry-run relay is read-only")
        path, query = pc.split_url(url)
        params = dict(query)
        if self.latency_s:
            import time as _t
            _t.sleep(self.latency_s)
        try:
            m = pc.TAPE_RE.match(path)
            if m:
                body = self.tape.serve(m.group(1), m.group(2), params)
                self._count("tape")
                return pc.FakeResponse(body)
            m = LATEST_RE.match(path)
            if m:
                row = self.tape.last_trade(m.group(1), self.now_ns())
                if row is None:
                    raise pc.ReplayMiss(f"no print for {m.group(1)} before now")
                self._count("latest")
                with self._lock:
                    self.log.append(("latest", m.group(1), self.now_ns()))
                return pc.FakeResponse(json.dumps({"symbol": m.group(1), "trade": row}).encode())
            key = pc.request_key(url)
            hit = self.exact.get(key)
            if hit is not None:
                status, body = hit
                self._count("exact")
                if status != 200:
                    import io
                    raise urllib.error.HTTPError(url, status, "recorded error", {}, io.BytesIO(body))
                return pc.FakeResponse(body)
            if path.endswith("/v2/stocks/bars") and self.bars.ok():
                body = self.bars.serve(params)
                self._count("bars_synth")
                with self._lock:
                    self.log.append(("bars", params.get("symbols"), self.now_ns()))
                return pc.FakeResponse(body)
            if path.endswith("/v1beta1/news"):
                body = self.news.serve(params)
                self._count("news_synth")
                with self._lock:
                    self.log.append(("news", params.get("symbols"), self.now_ns()))
                return pc.FakeResponse(body)
            raise pc.ReplayMiss(f"uncached request {key[:300]}")
        except pc.ReplayMiss as exc:
            with self._lock:
                self.misses.append(str(exc))
            raise


def validate(day: str) -> dict:
    """The synthesising stores must reproduce every recorded exact answer they could replace."""
    t = DryRunTransport(day, lambda: 0)
    out = {"bars_checked": 0, "bars_equal": 0, "bars_diff": [], "news_checked": 0, "news_equal": 0, "news_diff": []}
    for key, row in t.exact._index.items():
        path, q = key.split("?", 1)
        params = dict(urllib.parse.parse_qsl(q, keep_blank_values=True))
        body = json.loads(open(os.path.join(t.exact.dir, row["file"]), "rb").read())
        if path.endswith("/v2/stocks/bars") and params.get("timeframe") == "1Min" and t.bars.ok():
            out["bars_checked"] += 1
            got = json.loads(t.bars.serve(params))
            want = {k: v for k, v in (body.get("bars") or {}).items()}
            if got["bars"] == want and not body.get("next_page_token"):
                out["bars_equal"] += 1
            else:
                bad = [s for s in set(want) | set(got["bars"]) if want.get(s) != got["bars"].get(s)]
                out["bars_diff"].append({"key": key[:160], "symbols": sorted(bad)[:5]})
        elif path.endswith("/v1beta1/news") and not params.get("page_token"):
            # rebuild the full recorded answer (all pages) and compare with the synthesized one
            out["news_checked"] += 1
            ids, token, page_params = [], None, dict(params)
            while True:
                k = pc.request_key("https://x" + path + "?" + urllib.parse.urlencode(sorted(page_params.items())))
                hit = t.exact.get(k)
                if hit is None:
                    ids = None
                    break
                b = json.loads(hit[1])
                ids += [a["id"] for a in b.get("news") or []]
                token = b.get("next_page_token")
                if not token:
                    break
                page_params = dict(params, page_token=token)
            got = [a["id"] for a in json.loads(t.news.serve(params))["news"]]
            if ids is not None and ids == got:
                out["news_equal"] += 1
            elif ids is not None and sorted(ids) == sorted(got):
                out["news_equal"] += 1
                out.setdefault("news_order_only", 0)
                out["news_order_only"] = out.get("news_order_only", 0) + 1
            else:
                out["news_diff"].append({"key": key[:160], "recorded": None if ids is None else len(ids),
                                         "synth": len(got)})
    return out


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(d, json.dumps(validate(d)))
