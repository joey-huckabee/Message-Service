"""Tiny expectation DSL for demo scripts.

Lets a scenario assert "this should have happened" with clear
✓/✗ output and a non-zero exit code on any miss. The point isn't
elaborate test reporting — it's making the demo a smoke test by
default.

Usage:

    from examples._lib.expectations import Expectations

    expect = Expectations()
    expect.equals("recipient count", len(captured.rcpt_tos), 1)
    expect.contains("subject contains pipeline", captured.subject, "etl-nightly")
    expect.matches("body has run id", captured.body_text(), r"run [0-9a-f-]{32}")
    expect.report_and_exit()
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from examples._lib import pretty


@dataclass
class _Expectation:
    label: str
    passed: bool
    detail: str


class Expectations:
    """Collect expectation results; print a summary; exit non-zero on miss."""

    def __init__(self) -> None:
        self._results: list[_Expectation] = []

    def equals(self, label: str, actual: object, expected: object) -> None:
        passed = actual == expected
        self._record(label, passed, f"actual={actual!r}, expected={expected!r}")

    def contains(self, label: str, haystack: str, needle: str) -> None:
        passed = needle in haystack
        self._record(label, passed, f"needle={needle!r}")

    def matches(self, label: str, value: str, pattern: str) -> None:
        passed = re.search(pattern, value) is not None
        self._record(label, passed, f"pattern={pattern!r}")

    def truthy(self, label: str, value: object) -> None:
        passed = bool(value)
        self._record(label, passed, f"value={value!r}")

    def length(self, label: str, sequence: object, expected: int) -> None:
        try:
            actual = len(sequence)  # type: ignore[arg-type]
        except TypeError:
            self._record(label, False, f"value not lengthy: {sequence!r}")
            return
        self._record(
            label,
            actual == expected,
            f"actual={actual}, expected={expected}",
        )

    def at_least(self, label: str, sequence: object, minimum: int) -> None:
        try:
            actual = len(sequence)  # type: ignore[arg-type]
        except TypeError:
            self._record(label, False, f"value not lengthy: {sequence!r}")
            return
        self._record(
            label,
            actual >= minimum,
            f"actual={actual}, minimum={minimum}",
        )

    def _record(self, label: str, passed: bool, detail: str) -> None:
        self._results.append(_Expectation(label, passed, detail))
        if passed:
            pretty.success(label)
        else:
            pretty.failure(f"{label}  ({detail})")

    def report_and_exit(self) -> None:
        """Print summary; exit 0 if all passed, 1 if any failed."""
        passed = sum(1 for r in self._results if r.passed)
        total = len(self._results)
        pretty.header("Expectation summary")
        if passed == total:
            pretty.success(f"All {total} expectations passed.")
            sys.exit(0)
        else:
            pretty.failure(f"{total - passed} of {total} expectations FAILED.")
            for r in self._results:
                if not r.passed:
                    pretty.detail(f"  - {r.label}: {r.detail}")
            sys.exit(1)
