#!/usr/bin/env python3
"""SINAS Integration Test Runner.

Usage:
    python tests/integration/run.py                    # Run all tests
    python tests/integration/run.py --retry            # Retry only previously failed tests
    python tests/integration/run.py --suite auth       # Run specific suite
    python tests/integration/run.py --url https://...  # Override base URL
    python tests/integration/run.py --key sk-...       # Override API key (instead of .test-api-key file)

Setup:
    echo "sk-your-admin-api-key" > tests/integration/.test-api-key
"""

import argparse
import importlib
import json
import sys
import time
import traceback
from pathlib import Path

import httpx

STATE_FILE = Path(__file__).parent / ".test-state.json"
KEY_FILE = Path(__file__).parent / ".test-api-key"
DEFAULT_URL = "http://localhost:8000"

# Test result types
PASS = "pass"
FAIL = "fail"
SKIP = "skip"
ERROR = "error"

# Colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def status_icon(status: str) -> str:
    return {PASS: f"{GREEN}PASS{RESET}", FAIL: f"{RED}FAIL{RESET}", SKIP: f"{YELLOW}SKIP{RESET}", ERROR: f"{RED}ERR {RESET}"}[status]


class TestContext:
    """Shared context passed to all test suites."""

    def __init__(self, base_url: str, admin_key: str):
        self.base_url = base_url.rstrip("/")
        self.admin_key = admin_key
        self.client = httpx.Client(base_url=self.base_url, timeout=30.0)
        self._cleanup_tasks: list[callable] = []
        # Created during setup
        self.test_api_keys: dict[str, str] = {}

    def admin_headers(self) -> dict:
        return {"X-API-Key": self.admin_key}

    def key_headers(self, key_name: str) -> dict:
        return {"X-API-Key": self.test_api_keys[key_name]}

    def on_cleanup(self, fn: callable):
        """Register a cleanup function to run after all tests."""
        self._cleanup_tasks.append(fn)

    def cleanup(self):
        for fn in reversed(self._cleanup_tasks):
            try:
                fn()
            except Exception as e:
                print(f"  {DIM}cleanup error: {e}{RESET}")
        self.client.close()


class TestResult:
    def __init__(self, suite: str, name: str, status: str, message: str = "", duration: float = 0):
        self.suite = suite
        self.name = name
        self.status = status
        self.message = message
        self.duration = duration

    @property
    def full_name(self):
        return f"{self.suite}::{self.name}"

    def to_dict(self):
        return {"suite": self.suite, "name": self.name, "status": self.status, "message": self.message}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(results: list[TestResult]):
    state = {r.full_name: r.to_dict() for r in results}
    STATE_FILE.write_text(json.dumps(state, indent=2))


def discover_suites() -> list[str]:
    suites_dir = Path(__file__).parent / "suites"
    return sorted(
        f.stem for f in suites_dir.glob("*.py")
        if not f.name.startswith("_") and f.stem != "__init__"
    )


def run_suite(suite_name: str, ctx: TestContext, only_failed: set[str] | None = None) -> list[TestResult]:
    """Run all tests in a suite module."""
    module = importlib.import_module(f"suites.{suite_name}")
    results = []

    # Find all test functions (prefixed with test_)
    tests = [(name, fn) for name, fn in sorted(vars(module).items()) if name.startswith("test_") and callable(fn)]

    # Run suite setup if present
    if hasattr(module, "setup"):
        try:
            module.setup(ctx)
        except Exception as e:
            print(f"  {RED}Suite setup failed: {e}{RESET}")
            for name, _ in tests:
                results.append(TestResult(suite_name, name, ERROR, f"setup failed: {e}"))
            return results

    for name, fn in tests:
        full_name = f"{suite_name}::{name}"

        # Skip if retry mode and this test previously passed
        if only_failed is not None and full_name not in only_failed:
            results.append(TestResult(suite_name, name, PASS, "previously passed"))
            continue

        t0 = time.time()
        try:
            fn(ctx)
            duration = time.time() - t0
            results.append(TestResult(suite_name, name, PASS, duration=duration))
            print(f"  {status_icon(PASS)} {name} {DIM}({duration:.1f}s){RESET}")
        except AssertionError as e:
            duration = time.time() - t0
            results.append(TestResult(suite_name, name, FAIL, str(e), duration))
            print(f"  {status_icon(FAIL)} {name}: {e}")
        except Exception as e:
            duration = time.time() - t0
            results.append(TestResult(suite_name, name, ERROR, str(e), duration))
            print(f"  {status_icon(ERROR)} {name}: {e}")
            if "--verbose" in sys.argv:
                traceback.print_exc()

    # Run suite teardown if present
    if hasattr(module, "teardown"):
        try:
            module.teardown(ctx)
        except Exception as e:
            print(f"  {DIM}teardown error: {e}{RESET}")

    return results


def main():
    parser = argparse.ArgumentParser(description="SINAS Integration Tests")
    parser.add_argument("--url", default=DEFAULT_URL, help="Base URL")
    parser.add_argument("--key", default=None, help="Admin API key (overrides .test-api-key file)")
    parser.add_argument("--retry", action="store_true", help="Only re-run previously failed tests")
    parser.add_argument("--suite", default=None, help="Run specific suite only")
    parser.add_argument("--verbose", action="store_true", help="Show full tracebacks")
    args = parser.parse_args()

    # Load API key
    api_key = args.key
    if not api_key:
        if KEY_FILE.exists():
            api_key = KEY_FILE.read_text().strip()
        else:
            print(f"{RED}No API key. Create {KEY_FILE} or pass --key{RESET}")
            sys.exit(1)

    # Check connectivity
    print(f"\n{BOLD}SINAS Integration Tests{RESET}")
    print(f"{DIM}Target: {args.url}{RESET}\n")

    try:
        r = httpx.get(f"{args.url}/health", timeout=5)
        assert r.status_code == 200, f"Health check failed: {r.status_code}"
        print(f"{GREEN}Health check OK{RESET}\n")
    except Exception as e:
        print(f"{RED}Cannot reach {args.url}: {e}{RESET}")
        sys.exit(1)

    # Load retry state
    only_failed = None
    if args.retry:
        state = load_state()
        only_failed = {name for name, info in state.items() if info["status"] in (FAIL, ERROR)}
        if not only_failed:
            print(f"{GREEN}No failed tests to retry.{RESET}")
            sys.exit(0)
        print(f"{YELLOW}Retrying {len(only_failed)} failed test(s){RESET}\n")

    # Discover and run suites
    ctx = TestContext(args.url, api_key)
    all_results = []

    # Add suites dir to path so imports work
    suites_dir = str(Path(__file__).parent)
    if suites_dir not in sys.path:
        sys.path.insert(0, suites_dir)

    suite_names = [args.suite] if args.suite else discover_suites()

    try:
        for suite_name in suite_names:
            print(f"{BLUE}{BOLD}{suite_name}{RESET}")
            results = run_suite(suite_name, ctx, only_failed)
            all_results.extend(results)
            print()
    finally:
        ctx.cleanup()

    # Save state for retry
    save_state(all_results)

    # Report
    passed = sum(1 for r in all_results if r.status == PASS)
    failed = sum(1 for r in all_results if r.status == FAIL)
    errors = sum(1 for r in all_results if r.status == ERROR)
    total = len(all_results)
    total_time = sum(r.duration for r in all_results)

    print(f"{BOLD}{'=' * 50}{RESET}")
    print(f"{BOLD}Results:{RESET} {GREEN}{passed} passed{RESET}, ", end="")
    if failed:
        print(f"{RED}{failed} failed{RESET}, ", end="")
    if errors:
        print(f"{RED}{errors} errors{RESET}, ", end="")
    print(f"{total} total {DIM}({total_time:.1f}s){RESET}")

    if failed or errors:
        print(f"\n{YELLOW}Re-run failures with: python tests/integration/run.py --retry{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
