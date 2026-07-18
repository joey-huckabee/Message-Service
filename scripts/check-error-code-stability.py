#!/usr/bin/env python3
"""Gate error-code stability against the committed lockfile (L3-ERR-010 / L3-ERR-011).

Error codes are a client-compatibility contract: pipelines program against
specific ``UPPER_SNAKE_CASE`` codes (surfaced in gRPC trailing metadata under
``x-message-service-error-code``), so once a code ships it SHALL NOT be renamed
or repurposed (L2-ERR-005). This script pins that obligation by diffing the
current proto ``ErrorCode`` enum — the single enumerated set shared with the
exception hierarchy (L1-ERR-002), asserted at startup by ``L3-ERR-008`` — against
the committed lockfile at ``docs/error-codes.lock``.

Exit-code contract (L3-ERR-011):

* **0** — clean: the lockfile matches the current enum exactly.
* **1** — stability violation: one or more locked codes are absent from the
  current enum (a removal or rename). This is the never-allowed case.
* **2** — stale lockfile: the current enum declares codes not in the lockfile
  (an addition). Regenerate with ``update-error-codes-lock.py`` and commit.
* **3** — the lockfile is missing or unreadable.

Both 1 and 2 fail the CI gate, with distinct diagnostics so an operator can tell
a forbidden rename/removal from an intentional add that merely needs committing.

The comparison helpers (:func:`current_error_codes`, :func:`parse_lockfile`,
:func:`render_lockfile`, :func:`compare`) are importable — the companion
``update-error-codes-lock.py`` and the conformance tests load this module by
file path (its hyphenated CLI name blocks normal import) and reuse them, keeping
a single source of truth for the lockfile format.

Run from the project root::

    poetry run python scripts/check-error-code-stability.py
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCK_PATH = ROOT / "docs" / "error-codes.lock"

EXIT_OK = 0
EXIT_STABILITY_VIOLATION = 1
EXIT_STALE_LOCKFILE = 2
EXIT_LOCK_UNREADABLE = 3

_LOCK_HEADER = """\
# Message-Service error-code stability lockfile
#
# Locks the machine-readable error codes — the proto ErrorCode enum, the single
# enumerated set shared with the exception hierarchy per L1-ERR-002 and asserted
# at startup by L3-ERR-008. Per L2-ERR-005 / L3-ERR-010, once a code appears in a
# released version it SHALL NOT be renamed or repurposed.
#
# Regenerate with: poetry run python scripts/update-error-codes-lock.py
# CI gate:         poetry run python scripts/check-error-code-stability.py
#
# One UPPER_SNAKE_CASE code per line, sorted. '#'-prefixed and blank lines are
# ignored. DO NOT hand-edit — this file is generated.
"""


def current_error_codes() -> set[str]:
    """Return the error-code names declared by the current proto ``ErrorCode`` enum.

    Returns:
        The set of ``UPPER_SNAKE_CASE`` enum value names, matching what the
        bootstrap self-check reads via ``set(message_service_pb2.ErrorCode.keys())``.
    """
    from message_service_proto.v1 import message_service_pb2

    return set(message_service_pb2.ErrorCode.keys())


def parse_lockfile(text: str) -> set[str]:
    """Parse lockfile text into a set of codes, ignoring comments and blank lines.

    Args:
        text: Raw contents of a lockfile.

    Returns:
        The set of codes declared, one per non-comment, non-blank line.
    """
    codes: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        codes.add(stripped)
    return codes


def render_lockfile(codes: Iterable[str]) -> str:
    """Render the canonical lockfile text for a set of codes.

    Args:
        codes: The error codes to record.

    Returns:
        The lockfile contents: fixed header, a blank line, then each code on its
        own line sorted ascending, terminated by a trailing newline.
    """
    body = "\n".join(sorted(codes))
    return f"{_LOCK_HEADER}\n{body}\n"


@dataclass(frozen=True)
class Comparison:
    """Result of diffing the current enum against the lockfile.

    Attributes:
        added: Codes in the current enum but not the lockfile (stale lockfile).
        removed: Codes in the lockfile but not the current enum (stability
            violation — a removal or rename).
    """

    added: list[str]
    removed: list[str]

    @property
    def exit_code(self) -> int:
        """Return the exit code for this comparison per the L3-ERR-011 contract.

        A removal outranks an addition: a rename surfaces as one add and one
        remove, and the removal is the fatal, never-allowed condition.
        """
        if self.removed:
            return EXIT_STABILITY_VIOLATION
        if self.added:
            return EXIT_STALE_LOCKFILE
        return EXIT_OK


def compare(current: set[str], locked: set[str]) -> Comparison:
    """Diff the current enum against the locked set.

    Args:
        current: Codes declared by the current proto enum.
        locked: Codes recorded in the lockfile.

    Returns:
        A :class:`Comparison` naming the added and removed codes.
    """
    return Comparison(
        added=sorted(current - locked),
        removed=sorted(locked - current),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the stability check and return the process exit code.

    Args:
        argv: Unused; accepted for symmetry with argparse-style entry points.

    Returns:
        One of ``EXIT_OK`` / ``EXIT_STABILITY_VIOLATION`` / ``EXIT_STALE_LOCKFILE``
        / ``EXIT_LOCK_UNREADABLE``.
    """
    if not LOCK_PATH.exists():
        print(f"error: lockfile not found at {LOCK_PATH}", file=sys.stderr)
        print("hint: create it with scripts/update-error-codes-lock.py", file=sys.stderr)
        return EXIT_LOCK_UNREADABLE

    try:
        locked = parse_lockfile(LOCK_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"error: cannot read lockfile {LOCK_PATH}: {exc}", file=sys.stderr)
        return EXIT_LOCK_UNREADABLE

    result = compare(current_error_codes(), locked)

    if result.removed:
        print(
            "STABILITY VIOLATION: error code(s) removed or renamed since the "
            "lockfile was cut — these are frozen once released (L2-ERR-005):",
            file=sys.stderr,
        )
        for code in result.removed:
            print(f"  - {code}", file=sys.stderr)
    if result.added:
        stream = sys.stderr if result.removed else sys.stdout
        print(
            "STALE LOCKFILE: new error code(s) present in the proto enum but not "
            "the lockfile — regenerate with scripts/update-error-codes-lock.py "
            "and commit docs/error-codes.lock:",
            file=stream,
        )
        for code in result.added:
            print(f"  + {code}", file=stream)

    if result.exit_code == EXIT_OK:
        print(f"error-code lockfile OK: {len(locked)} codes, no drift")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
