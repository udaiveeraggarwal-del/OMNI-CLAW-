#!/usr/bin/env python3
"""
OmniAgent Unified E2E Test Runner.
Executes the multi-tier opaque-box E2E test suite across Tiers 1-4:
- Tier 1: Feature Coverage (>=5 tests per feature)
- Tier 2: Boundary & Corner Cases (>=5 tests per category)
- Tier 3: Cross-Feature Combinations (pairwise interactions)
- Tier 4: Real-World Application Scenarios (>=5 end-to-end workflows)

Usage:
    python tests/e2e/run_all_e2e.py
    python tests/e2e/run_all_e2e.py --tier 1
    python tests/e2e/run_all_e2e.py --verbose
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import unittest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.e2e.test_tier1_features import TestTier1FeatureCoverage
from tests.e2e.test_tier2_boundaries import TestTier2BoundariesAndCornerCases
from tests.e2e.test_tier3_combinations import TestTier3CrossFeatureCombinations
from tests.e2e.test_tier4_scenarios import TestTier4RealWorldApplicationScenarios


TIER_MAP = {
    1: ("Tier 1: Feature Coverage", TestTier1FeatureCoverage),
    2: ("Tier 2: Boundary & Corner Cases", TestTier2BoundariesAndCornerCases),
    3: ("Tier 3: Cross-Feature Combinations", TestTier3CrossFeatureCombinations),
    4: ("Tier 4: Real-World Application Scenarios", TestTier4RealWorldApplicationScenarios),
}


class ColoredText:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    @classmethod
    def green(cls, text: str) -> str:
        return f"{cls.GREEN}{text}{cls.RESET}"

    @classmethod
    def red(cls, text: str) -> str:
        return f"{cls.RED}{text}{cls.RESET}"

    @classmethod
    def yellow(cls, text: str) -> str:
        return f"{cls.YELLOW}{text}{cls.RESET}"

    @classmethod
    def cyan(cls, text: str) -> str:
        return f"{cls.CYAN}{text}{cls.RESET}"

    @classmethod
    def bold(cls, text: str) -> str:
        return f"{cls.BOLD}{text}{cls.RESET}"


def run_tier(tier_num: int, tier_name: str, test_case_cls: type, verbose: bool = False):
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(test_case_cls)
    total_tests = suite.countTestCases()

    start_time = time.time()
    stream = sys.stdout if verbose else open(os.devnull, "w")
    runner = unittest.TextTestRunner(stream=stream, verbosity=2 if verbose else 0)
    result = runner.run(suite)
    duration = time.time() - start_time

    if not verbose:
        stream.close()

    passed = total_tests - len(result.failures) - len(result.errors) - len(result.skipped)
    failures = len(result.failures)
    errors = len(result.errors)
    skipped = len(result.skipped)

    return {
        "tier": tier_num,
        "name": tier_name,
        "total": total_tests,
        "passed": passed,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "duration": duration,
        "result_obj": result,
    }


def main():
    parser = argparse.ArgumentParser(description="OmniAgent E2E Multi-Tier Test Runner")
    parser.add_argument("--tier", type=int, choices=[1, 2, 3, 4], help="Run only specified tier")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose test execution output")
    args = parser.parse_args()

    print("=" * 80)
    print(ColoredText.bold(ColoredText.cyan("  OmniAgent Universal Agentic Framework — E2E Test Suite Runner")))
    print("=" * 80)
    print(f"Working Directory : {PROJECT_ROOT}")
    print(f"Python Executable : {sys.executable}")
    print(f"Python Version    : {sys.version.split()[0]}")
    print("-" * 80)

    selected_tiers = [args.tier] if args.tier else [1, 2, 3, 4]
    tier_results = []
    total_start = time.time()

    for t_num in selected_tiers:
        t_name, t_cls = TIER_MAP[t_num]
        print(f"Running {ColoredText.bold(t_name)}...", end=" ", flush=True)
        res = run_tier(t_num, t_name, t_cls, verbose=args.verbose)
        tier_results.append(res)

        if res["failures"] == 0 and res["errors"] == 0:
            print(f"{ColoredText.green('PASSED')} ({res['passed']}/{res['total']} tests in {res['duration']:.2f}s)")
        else:
            print(f"{ColoredText.red('FAILED')} ({res['failures']} fail, {res['errors']} err in {res['duration']:.2f}s)")

    total_duration = time.time() - total_start

    print("\n" + "=" * 80)
    print(ColoredText.bold("  Multi-Tier E2E Test Suite Summary"))
    print("=" * 80)
    print(f"{'Tier':<8} {'Name':<42} {'Total':<8} {'Pass':<8} {'Fail':<8} {'Time (s)':<10}")
    print("-" * 80)

    total_count = 0
    total_passed = 0
    total_failed = 0
    total_errors = 0

    for tr in tier_results:
        total_count += tr["total"]
        total_passed += tr["passed"]
        total_failed += tr["failures"]
        total_errors += tr["errors"]

        status_color = ColoredText.green if (tr["failures"] == 0 and tr["errors"] == 0) else ColoredText.red
        print(
            f"Tier {tr['tier']:<3} {tr['name']:<42} "
            f"{tr['total']:<8} "
            f"{status_color(str(tr['passed'])):<17} "
            f"{tr['failures'] + tr['errors']:<8} "
            f"{tr['duration']:.2f}s"
        )

    print("-" * 80)
    print(
        f"{'TOTAL':<51} {total_count:<8} "
        f"{ColoredText.green(str(total_passed)) if total_failed == 0 and total_errors == 0 else ColoredText.red(str(total_passed)):<17} "
        f"{total_failed + total_errors:<8} "
        f"{total_duration:.2f}s"
    )
    print("=" * 80)

    # Print failure details if any
    has_issues = False
    for tr in tier_results:
        res_obj = tr["result_obj"]
        if res_obj.failures or res_obj.errors:
            has_issues = True
            print(ColoredText.bold(ColoredText.red(f"\nIssues in {tr['name']}:")))
            for test_case, err in res_obj.failures:
                print(f"  [FAIL] {test_case}:")
                for line in err.splitlines()[-4:]:
                    print(f"         {line}")
            for test_case, err in res_obj.errors:
                print(f"  [ERROR] {test_case}:")
                for line in err.splitlines()[-4:]:
                    print(f"         {line}")

    if not has_issues:
        print(f"\n{ColoredText.bold(ColoredText.green('ALL E2E TESTS PASSED SUCCESSFULLY! (100.0% Pass Rate)'))}\n")
        return 0
    else:
        print(f"\n{ColoredText.bold(ColoredText.red('E2E TEST SUITE ENCOUNTERED FAILURES.'))}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
