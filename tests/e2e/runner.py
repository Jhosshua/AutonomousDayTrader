#!/usr/bin/env python3
"""
AutonomousDayTrader E2E Test Suite Runner.

Can be run via:
- python3 tests/e2e/runner.py [--tier 1|2|3|4|all] [--feature F1..F21] [--verbose]
- pytest tests/e2e/

Features:
- Executes all collected E2E tests by default, including Tier 1-5 and visual checks; tier flags remain available for focused runs.
- Aggregates pass/fail metrics and feature coverage table
- Verifies port liberation and enforces process hygiene
- Produces clean terminal reports and optional JSON summary
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TESTS_E2E_DIR = PROJECT_ROOT / "tests" / "e2e"

TIER_FILES = {
    "1": TESTS_E2E_DIR / "test_tier1_features.py",
    "2": TESTS_E2E_DIR / "test_tier2_boundary.py",
    "3": TESTS_E2E_DIR / "test_tier3_pairwise.py",
    "4": TESTS_E2E_DIR / "test_tier4_scenarios.py",
    "5": TESTS_E2E_DIR / "test_tier5_adversarial.py",
}


def audit_ports(ports: List[int], timeout: float = 2.0) -> Dict[int, bool]:
    """Check if project ports are free, waiting up to timeout seconds for transient sockets to drain."""
    status = {}
    start = time.time()
    for p in ports:
        is_free = False
        while time.time() - start < timeout:
            try:
                res = subprocess.run(
                    ["lsof", "-tiTCP:" + str(p), "-sTCP:LISTEN"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if not bool(res.stdout.strip()):
                    is_free = True
                    break
            except Exception:
                is_free = True
                break
            time.sleep(0.2)
        status[p] = is_free
    return status


def run_tests(
    tier: str = "all",
    feature: Optional[str] = None,
    verbose: bool = False,
    json_report_path: Optional[str] = None,
) -> int:
    """Run E2E test suite using pytest."""
    import pytest

    args: List[str] = []

    # Select target test files
    if tier == "all":
        args.append(str(TESTS_E2E_DIR))
    elif tier in TIER_FILES:
        args.append(str(TIER_FILES[tier]))
    else:
        print(f"Error: Unknown tier '{tier}'. Choose from 1, 2, 3, 4, or all.")
        return 1

    # Feature filter
    if feature:
        feat_normalized = feature.lower()
        args.extend(["-k", feat_normalized])

    if verbose:
        args.append("-v")
    else:
        args.append("-q")

    # Disable warnings clutter
    args.extend(["-W", "ignore::DeprecationWarning"])

    print("=" * 70)
    print(" 🚀 AutonomousDayTrader Opaque-Box E2E Test Suite Runner")
    print(f" Target Tier: {tier.upper()} | Feature Filter: {feature or 'ALL (F1-F21)'}")
    print("=" * 70)

    start_time = time.monotonic()
    ret_code = pytest.main(args)
    duration = time.monotonic() - start_time

    # Verify ports after execution
    ports_to_check = [8080, 8005, 3005]
    port_status = audit_ports(ports_to_check)

    all_ports_clean = all(port_status.values())

    print("\n" + "=" * 70)
    print(" 📊 E2E TEST EXECUTION SUMMARY")
    print("=" * 70)
    print(f" Exit Code:        {ret_code} ({'SUCCESS - ALL PASSED' if ret_code == 0 else 'FAILED'})")
    print(f" Execution Time:   {duration:.2f} seconds")
    print(f" Port Hygiene:     {'ALL PORTS CLEAN & RELEASED' if all_ports_clean else 'WARNING: OCCUPIED PORTS DETECTED'}")
    for p, is_free in port_status.items():
        print(f"   - Port {p}: {'CLEAN (FREE)' if is_free else 'OCCUPIED'}")
    print("=" * 70)

    if json_report_path:
        report = {
            "exit_code": int(ret_code),
            "tier": tier,
            "feature": feature,
            "duration_seconds": round(duration, 3),
            "ports_audit": port_status,
            "timestamp": time.time(),
        }
        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f" Report written to: {json_report_path}")

    return int(ret_code) if all_ports_clean else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="AutonomousDayTrader E2E Test Runner")
    parser.add_argument("--tier", type=str, default="all", choices=["1", "2", "3", "4", "5", "all"], help="Test tier to execute")
    parser.add_argument("--feature", type=str, default=None, help="Filter by feature ID (e.g. F1, F5, F12)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose test output")
    parser.add_argument("--json-report", type=str, default=None, help="Path to write JSON test report")
    args = parser.parse_args()

    sys.exit(run_tests(
        tier=args.tier,
        feature=args.feature,
        verbose=args.verbose,
        json_report_path=args.json_report
    ))


if __name__ == "__main__":
    main()
