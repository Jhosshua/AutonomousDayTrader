"""Adversarial ORB tests: same guards as the ORB integration suite (no keys, no alpaca_paper mode, no relay
token, any real httpx request fails loudly) plus the real-main fixture."""
from backend.tests.unit.orb_integration.conftest import main_runtime, no_real_broker  # noqa: F401
