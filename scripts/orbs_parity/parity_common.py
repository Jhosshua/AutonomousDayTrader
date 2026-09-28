"""Record/replay HTTP transport shared by the ORBStraddle parity runners.

Two stores per session date under research/orbs_parity_cache/<date>/ (gitignored):

  tape/   raw SIP trades and quotes for every universe symbol, 09:30:00-10:15:00 ET, fetched ONCE from the
          relay (one row per line, compact JSON, relay order) plus a nanosecond timestamp index. Any
          /v2/stocks/{SYM}/trades|quotes request inside that window is answered by slicing this tape and
          paginating it like the relay (start inclusive, end inclusive, `limit` rows per page). Why not an
          exact request->response cache here: the incremental scanner re-reads overlapping windows every
          minute (about 60M rows per run), which would store the same prints dozens of times.
  exact/  every other relay request (calendar, assets, daily bars, SPY/QQQ and sector minute bars, news),
          keyed by path + sorted query, body stored byte-exact.

Replay mode never touches the network: a request neither store can answer is a MISS (recorded and
raised), so a parity run with misses fails. Floats are exact: JSON text -> float -> repr -> float is
the identity in Python.
"""
from __future__ import annotations

import bisect
import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from array import array
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_ROOT = os.environ.get("ADT_PARITY_CACHE_ROOT") or os.path.join(REPO, "research", "orbs_parity_cache")
MANIFEST = os.path.join(REPO, "backend", "app", "strategies", "orbs", "PARITY_MANIFEST.json")
SYNC_EDITS = os.path.join(REPO, "backend", "app", "strategies", "orbs", "SYNC_EDITS.json")
RELAY_ROOT = "https://alpacarelay-production.up.railway.app"
TAPE_START = dtime(9, 30)
TAPE_END = dtime(10, 15)
TAPE_RE = re.compile(r"^/data/v2/stocks/([A-Z][A-Z0-9.]{0,9})/(trades|quotes)$")
_REAL_URLOPEN = urllib.request.urlopen     # captured before any runner patches urlopen
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?([+-]\d{2}:\d{2})$")


def day_dir(day: str) -> str:
    return os.path.join(CACHE_ROOT, day)


def ts_ns(raw: str) -> int:
    raw = str(raw)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    m = _TS_RE.match(raw)
    if not m:
        raise ValueError(f"unparseable timestamp {raw!r}")
    base = datetime.fromisoformat(m.group(1) + m.group(3))
    frac = m.group(2) or ""
    ns = int((frac + "000000000")[:9]) if frac else 0
    return int(base.timestamp()) * 1_000_000_000 + ns


def split_url(url: str):
    """-> (path relative to the relay host, e.g. /data/v2/stocks/bars; sorted query list)."""
    parts = urllib.parse.urlsplit(url)
    return parts.path, sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))


def request_key(url: str) -> str:
    path, query = split_url(url)
    return path + "?" + urllib.parse.urlencode(query)


def read_token() -> str:
    tok = os.environ.get("RELAY_TOKEN", "").strip()
    if tok:
        return tok
    import subprocess
    out = subprocess.run(["railway", "variables", "--kv"], cwd="/Users/mo/AutonomousDayTrader",
                         capture_output=True, text=True, timeout=60).stdout
    for line in out.splitlines():
        if line.startswith("RELAY_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("RELAY_TOKEN not found (env or `railway variables --kv` in /Users/mo/AutonomousDayTrader)")


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}

    def getcode(self):
        return self.status


class ReplayMiss(RuntimeError):
    pass


# ---------------------------------------------------------------- tape store
class TapeStore:
    def __init__(self, day: str):
        self.day = day
        self.dir = os.path.join(day_dir(day), "tape")
        d = date.fromisoformat(day)
        self.start_ns = ts_ns(datetime.combine(d, TAPE_START, tzinfo=ET).isoformat())
        self.end_ns = ts_ns(datetime.combine(d, TAPE_END, tzinfo=ET).isoformat())
        self._cache = {}
        self._lock = threading.Lock()

    def paths(self, sym, kind):
        base = os.path.join(self.dir, f"{sym}.{kind}")
        return base + ".jsonl", base + ".ts"

    def has(self, sym, kind):
        rows, idx = self.paths(sym, kind)
        return os.path.exists(rows) and os.path.exists(idx)

    def write(self, sym, kind, rows):
        os.makedirs(self.dir, exist_ok=True)
        rows_path, idx_path = self.paths(sym, kind)
        stamps = array("q")
        with open(rows_path + ".tmp", "wb") as f:
            for row in rows:
                f.write(json.dumps(row, separators=(",", ":")).encode() + b"\n")
                stamps.append(ts_ns(row["t"]))
        with open(idx_path + ".tmp", "wb") as f:
            stamps.tofile(f)
        os.replace(rows_path + ".tmp", rows_path)
        os.replace(idx_path + ".tmp", idx_path)
        return len(stamps)

    def _load(self, sym, kind):
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
            raise ReplayMiss(f"tape index mismatch for {sym} {kind}")
        with self._lock:
            self._cache[key] = (lines, stamps)
        return lines, stamps

    def serve(self, sym, kind, params: dict) -> bytes:
        if not self.has(sym, kind):
            raise ReplayMiss(f"no tape for {sym} {kind}")
        if params.get("feed") != "sip" or params.get("sort", "asc") != "asc":
            raise ReplayMiss(f"tape serves feed=sip sort=asc only: {params}")
        start, end = ts_ns(params["start"]), ts_ns(params["end"])
        if start < self.start_ns or end > self.end_ns:
            raise ReplayMiss(f"{sym} {kind} window {params['start']}..{params['end']} outside the recorded tape")
        limit = int(params.get("limit", 10000))
        lines, stamps = self._load(sym, kind)
        lo = bisect.bisect_left(stamps, start)
        hi = bisect.bisect_right(stamps, end)
        token = params.get("page_token")
        i = lo
        if token:
            m = re.fullmatch(r"replay:(\d+)", token)
            if not m:
                raise ReplayMiss(f"foreign page token {token!r}")
            i = int(m.group(1))
        j = min(i + limit, hi)
        nxt = (b'"replay:%d"' % j) if j < hi else b"null"
        return (b'{"' + kind.encode() + b'":[' + b",".join(lines[i:j]) + b'],"symbol":"' + sym.encode()
                + b'","next_page_token":' + nxt + b"}")


# ---------------------------------------------------------------- exact store
class ExactStore:
    def __init__(self, day: str):
        self.dir = os.path.join(day_dir(day), "exact")
        self.index_path = os.path.join(self.dir, "index.jsonl")
        self._index = {}
        self._lock = threading.Lock()
        if os.path.exists(self.index_path):
            with open(self.index_path) as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        self._index[row["key"]] = row

    def get(self, key):
        row = self._index.get(key)
        if row is None:
            return None
        with open(os.path.join(self.dir, row["file"]), "rb") as f:
            return row["status"], f.read()

    def put(self, key, status, body):
        name = hashlib.sha256(key.encode()).hexdigest()[:32] + ".body"
        with self._lock:
            if key in self._index:
                return
            os.makedirs(self.dir, exist_ok=True)
            with open(os.path.join(self.dir, name), "wb") as f:
                f.write(body)
            row = {"key": key, "file": name, "status": status, "bytes": len(body)}
            with open(self.index_path, "a") as f:
                f.write(json.dumps(row) + "\n")
            self._index[key] = row


# ---------------------------------------------------------------- transport
class Transport:
    """urlopen(request, timeout=...) stand-in. mode 'replay' = stores only; 'record' = tape store for tape
    requests, exact store else, and a live relay fetch (saved) for exact misses."""

    def __init__(self, day: str, mode: str, token: str = ""):
        if mode not in ("replay", "record"):
            raise ValueError(mode)
        self.mode = mode
        self.tape = TapeStore(day)
        self.exact = ExactStore(day)
        self.token = token
        self.misses = []
        self.counts = {"tape": 0, "exact": 0, "live": 0}
        self._lock = threading.Lock()

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        method = req.get_method() if hasattr(req, "get_method") else "GET"
        if method != "GET":
            raise PermissionError("parity transport is read-only")
        path, query = split_url(url)
        params = dict(query)
        m = TAPE_RE.match(path)
        try:
            if m:
                body = self.tape.serve(m.group(1), m.group(2), params)
                with self._lock:
                    self.counts["tape"] += 1
                return FakeResponse(body)
            key = request_key(url)
            hit = self.exact.get(key)
            if hit is None and self.mode == "record":
                hit = self._live(url, key)
            if hit is None:
                raise ReplayMiss(f"uncached request {key[:300]}")
            status, body = hit
            with self._lock:
                self.counts["exact"] += 1
            if status != 200:
                raise urllib.error.HTTPError(url, status, "recorded error", {}, io.BytesIO(body))
            return FakeResponse(body)
        except ReplayMiss as exc:
            with self._lock:
                self.misses.append(str(exc))
            raise

    def _live(self, url, key):
        path, query = split_url(url)
        if os.environ.get("ADT_PARITY_SYNTHETIC") == "1":
            import parity_synthetic as synthetic      # the checked-in synthetic session answers instead of the relay
            status, body = synthetic.respond(path, dict(query))
            self.exact.put(key, status, body)
            with self._lock:
                self.counts["live"] += 1
            return status, body
        live_url = RELAY_ROOT + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"X-Relay-Token": self.token, "Accept": "application/json", "User-Agent": "ADT-ORBS-parity/1.0"}
        last = None
        for attempt in range(4):
            try:
                with _REAL_URLOPEN(urllib.request.Request(live_url, headers=headers), timeout=60) as r:
                    body = r.read()
                    status = r.status
                json.loads(body)
                self.exact.put(key, status, body)
                with self._lock:
                    self.counts["live"] += 1
                return status, body
            except urllib.error.HTTPError as exc:
                if exc.code in (400, 401, 403, 404, 422):
                    body = exc.read()
                    self.exact.put(key, exc.code, body)
                    return exc.code, body
                last = exc
            except Exception as exc:  # transport glitch: retry
                last = exc
            time.sleep(1.5 * (attempt + 1))
        raise ReplayMiss(f"live fetch failed for {key[:200]}: {last}")


# ---------------------------------------------------------------- tape recording
def fetch_tape(transport_token: str, day: str, sym: str, kind: str):
    """All SIP rows of one symbol and kind in the tape window, straight from the relay."""
    d = date.fromisoformat(day)
    params = {"start": datetime.combine(d, TAPE_START, tzinfo=ET).isoformat(),
              "end": datetime.combine(d, TAPE_END, tzinfo=ET).isoformat(),
              "limit": 10000, "feed": "sip", "sort": "asc"}
    headers = {"X-Relay-Token": transport_token, "Accept": "application/json", "User-Agent": "ADT-ORBS-parity/1.0"}
    rows, token, seen = [], None, set()
    for _ in range(2000):
        q = dict(params)
        if token:
            q["page_token"] = token
        url = f"{RELAY_ROOT}/data/v2/stocks/{urllib.parse.quote(sym)}/{kind}?" + urllib.parse.urlencode(q)
        last = None
        for attempt in range(5):
            try:
                with _REAL_URLOPEN(urllib.request.Request(url, headers=headers), timeout=90) as r:
                    page = json.loads(r.read())
                break
            except Exception as exc:
                last = exc
                time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(f"{sym} {kind}: page fetch failed: {last}")
        rows.extend(page.get(kind) or [])
        token = page.get("next_page_token")
        if not token:
            return rows
        if token in seen:
            raise RuntimeError(f"{sym} {kind}: repeated page token")
        seen.add(token)
    raise RuntimeError(f"{sym} {kind}: too many pages")


# ---------------------------------------------------------------- schedule shared by both runners
def schedule(day: str):
    """ORBStraddle's live timeline (app.py:283-373): preview 09:36 (at 09:36:10), final 09:38 (at 09:38:30),
    primary decision right after (pinned 09:39:00), then a secondary scan each minute 09:45..10:15 with its
    decision pinned 30 s after the scan end. Live never scans 10:15 itself (loop runs while now < 10:15);
    it is included because the plan asks for 09:45..10:15 and it costs nothing for parity."""
    d = date.fromisoformat(day)

    def at(h, m, s=0):
        return datetime.combine(d, dtime(h, m, s), tzinfo=ET)

    steps = [{"wave": "preview", "end": "09:36", "scan_now": at(9, 36, 10), "decide_now": None},
             {"wave": "primary", "end": "09:38", "scan_now": at(9, 38, 30), "decide_now": at(9, 39, 0)}]
    t = at(9, 45)
    while t <= at(10, 15):
        steps.append({"wave": "secondary", "end": t.strftime("%H:%M"), "scan_now": t + timedelta(seconds=5),
                      "decide_now": t + timedelta(seconds=30)})
        t += timedelta(minutes=1)
    return steps


HEALTH_KEYS = ("attempted", "ok", "failed", "failed_symbols", "cards", "coverage", "retried_symbols",
               "recovered_symbols", "day", "end", "source")


def health_subset(health):
    return {k: (health or {}).get(k) for k in HEALTH_KEYS}


def dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def load_manifest():
    with open(MANIFEST) as f:
        return json.load(f)
