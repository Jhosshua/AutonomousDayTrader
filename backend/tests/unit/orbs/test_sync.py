"""Sync check: the copied modules are ORBStraddle@06ca29f plus ONLY the edits listed in SYNC_EDITS.json.

With the ORBStraddle checkout present (the operator's Mac) this fails when ORBStraddle's files drift from
the pinned commit (re-sync needed) or when a copy differs from original + documented edits. Without it
(CI/Railway) the structural checks still run.
"""
import ast
import os

import pytest

from backend.app.strategies.orbs import _sync
from backend.tests.unit.orbs._helpers import ORBSTRADDLE_DIR

SPEC = _sync.load_spec()
HAVE_SOURCE = os.path.exists(os.path.join(ORBSTRADDLE_DIR, "scanner.py"))
SHIM_VERBATIM = ("_valid_card", "_sanitize", "_json_objects", "_extract_json", "validate", "_num")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_every_copy_has_its_generated_header_and_edits():
    for name in _sync.COPIED_FILES:
        text = _read(os.path.join(_sync.PKG_DIR, name))
        head, body = _sync.split_header(text)
        assert head == _sync.header(name, SPEC), name
        assert f"@{SPEC['source_commit']}; logic unchanged" in head
        for edit in SPEC["files"][name]:
            assert body.count(edit["new"]) >= edit["count"], (name, edit["why"])
            if edit["old"] not in edit["new"]:
                assert body.count(edit["old"]) == 0, (name, "an original fragment survived", edit["old"])


def test_copies_never_import_orbstraddle_top_level_modules():
    for name in _sync.COPIED_FILES:
        tree = ast.parse(_read(os.path.join(_sync.PKG_DIR, name)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("config", "core", "scanner", "flow", "adaptive", "signals", "market",
                                              "orbproc", "ticks", "orders", "auditor", "session_calendar"), (name, alias.name)
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert node.module not in ("ticks", "config", "core", "session_calendar"), (name, node.module)


@pytest.mark.skipif(not HAVE_SOURCE, reason="ORBStraddle checkout not available")
def test_orbstraddle_source_is_still_the_pinned_commit():
    problems = _sync.check_sources(ORBSTRADDLE_DIR, SPEC)
    assert not problems, ("ORBStraddle changed since the copy was made (re-sync the copies, re-run the parity "
                          f"harness and update SYNC_EDITS.json): {problems}")


@pytest.mark.skipif(not HAVE_SOURCE, reason="ORBStraddle checkout not available")
def test_copies_equal_original_plus_documented_edits():
    if _sync.check_sources(ORBSTRADDLE_DIR, SPEC):
        pytest.fail("ORBStraddle source drifted; see test_orbstraddle_source_is_still_the_pinned_commit")
    assert _sync.check_copies(ORBSTRADDLE_DIR, SPEC) == []


@pytest.mark.skipif(not HAVE_SOURCE, reason="ORBStraddle checkout not available")
def test_shim_validator_functions_are_verbatim_core_py():
    core_src = _read(os.path.join(ORBSTRADDLE_DIR, "core.py"))
    shim_src = _read(os.path.join(_sync.PKG_DIR, "shim.py"))

    def segments(src):
        tree = ast.parse(src)
        return {n.name: ast.get_source_segment(src, n) for n in tree.body if isinstance(n, ast.FunctionDef)}

    core_fns, shim_fns = segments(core_src), segments(shim_src)
    for name in SHIM_VERBATIM:
        assert shim_fns[name] == core_fns[name], name
