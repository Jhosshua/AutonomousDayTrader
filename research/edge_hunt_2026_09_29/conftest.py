# @steered SNARE-2 2026-09-29
"""Lets the research tests run from the repo root (`pytest research/edge_hunt_2026_09_29`) and keeps
a plain root `pytest` safe. The research needs numpy and scipy (its own .venv). Where they are
missing, as in the backend environment, the research test module is skipped instead of failing."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

try:
    import numpy  # noqa: F401
    import scipy  # noqa: F401
except ImportError:
    collect_ignore = ["test_research.py"]
