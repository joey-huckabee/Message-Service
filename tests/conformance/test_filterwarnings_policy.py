"""Conformance: ``pyproject.toml`` filterwarnings policy.

Pins L3-CICD-004: the first entry of ``tool.pytest.ini_options.
filterwarnings`` SHALL be ``"error"``, escalating every Python
warning to a test failure unless explicitly allow-listed by a
subsequent entry.

Also asserts the existing allow-list is small and documented — new
ignores SHOULD come with an inline comment explaining the upstream
issue (this test can't enforce comments inside a TOML array, but it
caps the allow-list size as a soft proxy: large allow-list growth
forces a deliberate update to this cap).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _filterwarnings() -> list[str]:
    config = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return list(config["tool"]["pytest"]["ini_options"]["filterwarnings"])


@pytest.mark.requirement("L3-CICD-004")
def test_filterwarnings_starts_with_error() -> None:
    """L3-CICD-004: the first entry SHALL be ``"error"`` so every
    warning escalates to a test failure unless explicitly allow-listed
    by a subsequent entry."""
    entries = _filterwarnings()
    assert entries, "filterwarnings must not be empty"
    assert entries[0] == "error", (
        f"filterwarnings[0] SHALL be 'error' (per L3-CICD-004); got {entries[0]!r}"
    )


@pytest.mark.requirement("L3-CICD-004")
def test_filterwarnings_allow_list_is_small() -> None:
    """A growing allow-list erodes the warning-escalation contract.
    Cap the count so deliberate growth requires a spec discussion."""
    entries = _filterwarnings()
    # Position 0 is "error" (the escalator); everything after is an
    # allow-list entry.
    allow_list = entries[1:]
    assert len(allow_list) <= 5, (
        f"filterwarnings allow-list SHALL stay small (≤ 5 entries) "
        f"to keep the escalation contract meaningful. Currently has "
        f"{len(allow_list)} entries: {allow_list}. "
        f"If this test fails because a new ignore is genuinely needed, "
        f"raise the cap deliberately and document the upstream issue."
    )
