"""Shared helpers for the ORBStraddle-copy tests (no network anywhere: replay transports and fakes only)."""
import importlib
import io
import json
import os
import sys
import tarfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
PARITY_DIR = os.path.join(REPO, "scripts", "orbs_parity")
FIXTURE_TGZ = os.path.join(os.path.dirname(__file__), "fixtures", "parity_synthetic_2031-03-04.tar.gz")
SYNTH_DAY = "2031-03-04"
# The ORBStraddle source the pinned-commit checks read. ADT_ORBSTRADDLE_DIR may point at a clean export of the
# pinned commit (`git -C /Users/mo/ORBStraddle archive <pin> | tar -x -C <dir>`) when the live checkout has
# moved past the pin; the default is the operator's checkout, so drift past the pin is visible by default.
ORBSTRADDLE_DIR = os.environ.get("ADT_ORBSTRADDLE_DIR") or "/Users/mo/ORBStraddle"


def parity_modules():
    """(parity_common, copy_runner, compare) from scripts/orbs_parity."""
    if PARITY_DIR not in sys.path:
        sys.path.insert(0, PARITY_DIR)
    return (importlib.import_module("parity_common"), importlib.import_module("copy_runner"),
            importlib.import_module("compare"))


def unpack_fixture(dest):
    with tarfile.open(FIXTURE_TGZ, "r:gz") as tar:
        tar.extractall(dest)
    return os.path.join(dest, SYNTH_DAY)


def load_golden(day_dir):
    with open(os.path.join(day_dir, "golden_original.json")) as f:
        return json.load(f)


class FakeResp(io.BytesIO):
    def __init__(self, obj, status=200):
        body = json.dumps(obj).encode()
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Length": str(len(body))}


class FakeHTTP:
    """urlopen stand-in: routes by URL path to a handler(path, params) -> JSON-able object."""

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def __call__(self, req, timeout=None):
        import urllib.parse
        url = req.full_url if hasattr(req, "full_url") else str(req)
        parts = urllib.parse.urlsplit(url)
        params = dict(urllib.parse.parse_qsl(parts.query))
        self.calls.append((parts.path, params))
        return FakeResp(self.handler(parts.path, params))
