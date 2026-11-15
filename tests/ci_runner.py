"""Unattended test runner with a per-test deadlock watchdog.

GUI tests can open a modal dialog if their mocks are outdated. Unittest
otherwise waits for user interaction forever in GitHub Actions.
"""
from __future__ import annotations

import faulthandler
import sys
import unittest
from pathlib import Path


TEST_TIMEOUT_SECONDS = 180


class TimedTestResult(unittest.TextTestResult):
    def startTest(self, test: unittest.case.TestCase) -> None:
        super().startTest(test)
        # Uses a watchdog thread; still fires if the Qt event loop is blocked
        # inside a native modal dialog. Dump the test's Python stack before exit.
        faulthandler.dump_traceback_later(
            TEST_TIMEOUT_SECONDS, repeat=False, exit=True,
        )

    def stopTest(self, test: unittest.case.TestCase) -> None:
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)


def main() -> int:
    tests_dir = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(tests_dir), pattern="test_*.py",
    )
    result = unittest.TextTestRunner(
        verbosity=2, resultclass=TimedTestResult,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
