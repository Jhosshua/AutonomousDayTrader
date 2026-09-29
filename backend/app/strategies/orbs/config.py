"""Configuration for ADT's copy of ORBStraddle's decision code.

Every trading value comes from PARITY_MANIFEST.json (ORBStraddle's LIVE effective values at the pinned
commit), never from ORBStraddle's code defaults and never from the process environment. The copied
modules read this module exactly the way ORBStraddle's modules read ORBStraddle's config.py
(`config.CANDLE_RULE`, `config.RELAY_DATA`, ...). The relay base URL and token come from ADT's own
settings through configure(); the ADT-only `EXCLUDE_SYMBOLS` list is also set there.

Module-level state: the copied modules are module singletons, so one process runs one configuration.
"""
from __future__ import annotations

import copy
import json
import os
from typing import Any, Iterable, Optional

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PARITY_MANIFEST.json")


def load_manifest(path: Optional[str] = None) -> dict:
    with open(path or MANIFEST_PATH) as handle:
        return json.load(handle)


MANIFEST: dict = {}
SCANNER_ENV: dict = {}
EXCLUDE_SYMBOLS: frozenset = frozenset()

# Relay endpoints: empty until configure() is called, so an unconfigured copy fails closed.
RELAY_DATA = ""
RELAY_META = ""
RELAY_TOKEN = ""
RELAY_UA = "ADT-ORBS/1.0"
RELAY_HEADERS = {"X-Relay-Token": "", "Accept": "application/json", "User-Agent": RELAY_UA}
NEWS_URL = ""
INDEX_BARS_URL = ""

# State directory (signals.get_lockout_filepath reads config.STATE; scanner.STATE = ROOT/state at import).
ROOT = "/data/orbs"
STATE = os.path.join(ROOT, "state")

_TUPLE_KEYS = ("FLATTEN_ESCALATE_HM",)


def apply_manifest(manifest: dict) -> None:
    """Set every effective value from a manifest dict (validated: the live flow/candle keys must exist)."""
    global MANIFEST, SCANNER_ENV, EXCLUDE_SYMBOLS, ROOT, STATE
    effective = manifest["effective"]
    required = ("CANDLE_RULE", "DELTA_RULE", "VELOCITY_RULE", "MACRO_RULE", "ABSORPTION_EXIT", "FREEZE_ET",
                "MIN_SCAN_COVERAGE", "EARN_REPRICE_GAP", "MAX_PICKS", "MAX_STRUCTURE", "SECONDARY_WAVE_START")
    missing = [k for k in required if k not in effective]
    if missing:
        raise ValueError(f"parity manifest lacks {missing}")
    for key, value in effective.items():
        globals()[key] = tuple(value) if key in _TUPLE_KEYS else value
    mode = str(effective.get("LONG_ONLY_MODE", "")).strip().lower()
    if mode not in ("", "yes", "no"):
        # ORBStraddle's "auto" reads the broker account; ADT's copy has no account access in phase 1.
        raise ValueError(f"LONG_ONLY_MODE {mode!r} is not supported by ADT's copy")
    SCANNER_ENV = {k: v for k, v in manifest.get("scanner_env", {}).items() if not k.startswith("_")}
    adt = manifest.get("adt", {})
    EXCLUDE_SYMBOLS = frozenset(str(s).upper() for s in (adt.get("exclude_symbols") or ()))
    ROOT = adt.get("state_dir_default") or ROOT
    STATE = os.path.join(ROOT, "state")
    MANIFEST = copy.deepcopy(manifest)


def getenv(name: str, default: Any = None) -> Any:
    """Stand-in for os.environ.get in the copied modules: answers ONLY from the manifest's scanner_env.
    A pinned null means 'unset' (the caller's default applies). An unpinned name is an error, so no
    value can silently fall back to an ORBStraddle code default."""
    if name not in SCANNER_ENV:
        raise KeyError(f"{name} is not pinned in PARITY_MANIFEST.json scanner_env")
    value = SCANNER_ENV[name]
    return default if value is None else value


def set_relay(base: str, token: str, ua: Optional[str] = None) -> None:
    """base = ADT's RELAY_HTTP_URL (the relay root, e.g. https://alpacarelay-production.up.railway.app)."""
    global RELAY_DATA, RELAY_META, RELAY_TOKEN, RELAY_UA, RELAY_HEADERS, NEWS_URL, INDEX_BARS_URL
    root = str(base or "").rstrip("/")
    if root.endswith("/data"):
        root = root[: -len("/data")]
    RELAY_DATA = root + "/data" if root else ""
    RELAY_META = root + "/metadata" if root else ""
    RELAY_TOKEN = token or ""
    RELAY_UA = ua or RELAY_UA
    RELAY_HEADERS = {"X-Relay-Token": RELAY_TOKEN, "Accept": "application/json", "User-Agent": RELAY_UA}
    NEWS_URL = RELAY_DATA + "/v1beta1/news"
    INDEX_BARS_URL = RELAY_DATA + "/v2/stocks/bars"


def set_exclude_symbols(symbols: Optional[Iterable[str]]) -> None:
    global EXCLUDE_SYMBOLS
    EXCLUDE_SYMBOLS = frozenset(str(s).upper() for s in (symbols or ()))


def set_state_dir(path: str) -> None:
    global ROOT, STATE
    STATE = os.path.abspath(path)
    ROOT = os.path.dirname(STATE)


def effective() -> dict:
    """The copy's effective values for every key the manifest pins (tests compare this to the manifest)."""
    out = {}
    for key in MANIFEST.get("effective", {}):
        value = globals()[key]
        out[key] = list(value) if isinstance(value, tuple) else value
    return out


apply_manifest(load_manifest())
