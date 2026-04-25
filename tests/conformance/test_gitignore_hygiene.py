"""Conformance: ``.gitignore`` enforces critical test/dev-tooling exclusions.

Pins L3-CICD-014: ``.pytest_tmp/`` SHALL be present as an explicit
entry in ``.gitignore``, not merely matched by a broader glob. A
single forgotten cleanup would otherwise add tens or hundreds of
test-artifact files to the next commit.

Also asserts the partner exclusions for the other CI-relevant
artifact directories (coverage, mypy cache, ruff cache) are present
so a contributor doesn't accidentally commit them either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_GITIGNORE = Path(__file__).resolve().parents[2] / ".gitignore"


def _gitignore_lines() -> list[str]:
    """Return non-comment, non-blank ``.gitignore`` lines, stripped."""
    text = _GITIGNORE.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@pytest.mark.requirement("L3-CICD-014")
def test_pytest_tmp_is_explicitly_ignored() -> None:
    """L3-CICD-014: ``.pytest_tmp/`` (with trailing slash) SHALL be a
    literal entry, not just matched by a wildcard like ``.pytest*``."""
    lines = _gitignore_lines()
    assert ".pytest_tmp/" in lines, (
        ".gitignore SHALL contain '.pytest_tmp/' as an explicit entry "
        "(per L3-CICD-014). The trailing slash matters: it limits the "
        "ignore to directory matches and documents intent for readers."
    )


def test_other_ci_artifact_dirs_are_ignored() -> None:
    """Coverage, mypy cache, ruff cache, pytest cache — all CI/dev
    state that should never enter source control. If any of these
    leak, ``--basetemp=.pytest_tmp`` is the only one CI can recover
    from cleanly."""
    lines = _gitignore_lines()
    expected = {
        ".coverage",
        ".coverage.*",
        ".coverage_html/",
        ".coverage.xml",
        ".mypy_cache/",
        ".ruff_cache/",
        ".pytest_cache/",
        ".pytest_tmp/",
    }
    missing = expected - set(lines)
    assert not missing, f".gitignore is missing expected CI-artifact entries: {sorted(missing)}"


def test_claude_tooling_state_is_ignored() -> None:
    """Per-workspace AI-tooling state (`.claude/`) is contributor-local
    and SHALL NOT be committed."""
    lines = _gitignore_lines()
    assert ".claude/" in lines
