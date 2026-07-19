#!/usr/bin/env python3
"""Gate on per-L1 requirement coverage (L2-CICD-016 / L3-CICD-018).

The aggregate ``--cov-fail-under`` gate enforces line/branch coverage but cannot
see that an entire L1 requirement has *zero* linked verification artifacts: no
test in its subtree carries a ``@pytest.mark.requirement`` marker for any of its
L2/L3 statements. This gate closes that gap by reading the committed
``docs/TRACE-MATRIX.md`` (kept fresh by the ``build-trace-matrix.py --check``
gate), collecting every L1 whose rolled-up status is ``Draft``, and failing if
any such L1 is not recorded — with a rationale — on the deferral allowlist at
``docs/uncovered-l1-allowlist.toml``.

Exit-code contract (L3-CICD-018):

* **0** — clean: every ``Draft`` L1 is on the allowlist (or there are none).
* **1** — uncovered: one or more ``Draft`` L1s are not allowlisted (each named).
* **2** — the matrix or allowlist could not be read or parsed.

The parsing/comparison helpers (:func:`parse_l1_statuses`, :func:`parse_allowlist`,
:func:`uncovered_l1s`) are importable so the conformance test can exercise the
clean / uncovered / unreadable outcomes without shelling out.

Run from the project root::

    poetry run python scripts/check-requirement-coverage.py
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_MATRIX_PATH = ROOT / "docs" / "TRACE-MATRIX.md"
ALLOWLIST_PATH = ROOT / "docs" / "uncovered-l1-allowlist.toml"

EXIT_OK = 0
EXIT_UNCOVERED = 1
EXIT_UNREADABLE = 2

_L1_ROW = re.compile(r"^\|\s*(L1-[A-Z]+-\d+)\s*\|[^|]*\|\s*([A-Za-z ]+?)\s*\|\s*$", re.MULTILINE)
_L1_ID = re.compile(r"^L1-[A-Z]+-\d+$")


class AllowlistError(ValueError):
    """The allowlist is malformed (e.g. a missing/empty ``reason``)."""


def parse_l1_statuses(matrix_text: str) -> dict[str, str]:
    """Extract ``{l1_id: status}`` from the L1 rows of a trace matrix.

    Args:
        matrix_text: Contents of ``docs/TRACE-MATRIX.md``.

    Returns:
        Mapping of every ``L1-<CAT>-<NNN>`` id to its rolled-up status string.
    """
    return {match.group(1): match.group(2) for match in _L1_ROW.finditer(matrix_text)}


def parse_allowlist(allowlist_text: str) -> set[str]:
    """Parse the deferral allowlist into a set of allowed L1 ids.

    Args:
        allowlist_text: Contents of ``docs/uncovered-l1-allowlist.toml``.

    Returns:
        The set of L1 ids permitted to be ``Draft``.

    Raises:
        AllowlistError: An entry is missing its ``id``/``reason``, has an empty
            ``reason``, or an ``id`` not matching ``L1-<CAT>-<NNN>``.
    """
    data = tomllib.loads(allowlist_text)
    allowed: set[str] = set()
    for entry in data.get("allowed", []):
        entry_id = entry.get("id") if isinstance(entry, dict) else None
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if not isinstance(entry_id, str) or not _L1_ID.match(entry_id):
            raise AllowlistError(f"allowlist entry has a missing or malformed id: {entry!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise AllowlistError(f"allowlist entry {entry_id!r} has a missing or empty reason")
        allowed.add(entry_id)
    return allowed


def uncovered_l1s(statuses: dict[str, str], allowed: set[str]) -> list[str]:
    """Return the sorted ``Draft`` L1 ids that are not on the allowlist.

    Args:
        statuses: Mapping from :func:`parse_l1_statuses`.
        allowed: Set from :func:`parse_allowlist`.

    Returns:
        Sorted list of uncovered, non-allowlisted L1 ids (empty when clean).
    """
    draft = {l1_id for l1_id, status in statuses.items() if status == "Draft"}
    return sorted(draft - allowed)


def main(argv: list[str] | None = None) -> int:
    """Run the coverage gate and return the process exit code.

    Args:
        argv: Unused; accepted for argparse-style symmetry.

    Returns:
        One of ``EXIT_OK`` / ``EXIT_UNCOVERED`` / ``EXIT_UNREADABLE``.
    """
    try:
        matrix_text = TRACE_MATRIX_PATH.read_text(encoding="utf-8")
        allowlist_text = ALLOWLIST_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read a required file: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        allowed = parse_allowlist(allowlist_text)
    except (tomllib.TOMLDecodeError, AllowlistError) as exc:
        print(f"error: cannot parse {ALLOWLIST_PATH}: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    statuses = parse_l1_statuses(matrix_text)
    if not statuses:
        print(f"error: no L1 rows found in {TRACE_MATRIX_PATH}", file=sys.stderr)
        return EXIT_UNREADABLE

    uncovered = uncovered_l1s(statuses, allowed)
    if uncovered:
        print(
            "requirement-coverage FAILURE: L1 requirement(s) have no linked "
            "verification artifact and are not on the deferral allowlist "
            f"({ALLOWLIST_PATH.name}):",
            file=sys.stderr,
        )
        for l1_id in uncovered:
            print(f"  - {l1_id}", file=sys.stderr)
        return EXIT_UNCOVERED

    draft_count = sum(1 for s in statuses.values() if s == "Draft")
    print(
        f"requirement coverage OK: {len(statuses)} L1s, "
        f"{draft_count} allowlisted Draft, 0 uncovered"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
