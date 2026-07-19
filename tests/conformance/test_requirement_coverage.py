"""Conformance: the per-L1 requirement-coverage gate (L3-CICD-018 / L3-CICD-019).

Supersedes the former env-var-gated marker-scan stub: requirement coverage is
now enforced concretely by ``scripts/check-requirement-coverage.py``, which reads
the committed ``docs/TRACE-MATRIX.md`` and fails on any ``Draft`` L1 not on
``docs/uncovered-l1-allowlist.toml``. (Marker-id existence and Parent-field
integrity are already enforced by ``build-trace-matrix.py``.)

The script's filename uses hyphens (CLI convention), so it is loaded via
importlib spec rather than a normal import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "check-requirement-coverage.py"
_ALLOWLIST = _ROOT / "docs" / "uncovered-l1-allowlist.toml"


def _load() -> ModuleType:
    """Load the hyphen-named script as a module by file path."""
    spec = importlib.util.spec_from_file_location("check_requirement_coverage", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_M = _load()

_MATRIX_FIXTURE = """\
**L1 → L2**

| L1 ID | L2 Children | Status |
|-------|-------------|--------|
| L1-RUN-001 | L2-RUN-001 | Implemented |
| L1-DASH-004 | L2-DASH-010, L2-DASH-011 | Draft |
| L1-FOO-009 | L2-FOO-001 | Partially Implemented |
"""


# -----------------------------------------------------------------------------
# L3-CICD-018 — matrix parse + uncovered-set computation
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-018")
def test_script_exists() -> None:
    """The check script ships at its documented path."""
    assert _SCRIPT.is_file()


@pytest.mark.requirement("L3-CICD-018")
def test_parse_l1_statuses_extracts_each_l1_row() -> None:
    """Only L1 rows are parsed, with their status (incl. multi-word)."""
    statuses = _M.parse_l1_statuses(_MATRIX_FIXTURE)
    assert statuses == {
        "L1-RUN-001": "Implemented",
        "L1-DASH-004": "Draft",
        "L1-FOO-009": "Partially Implemented",
    }


@pytest.mark.requirement("L3-CICD-018")
def test_uncovered_excludes_allowlisted_draft() -> None:
    """A Draft L1 on the allowlist is not reported; others are."""
    statuses = _M.parse_l1_statuses(_MATRIX_FIXTURE)
    assert _M.uncovered_l1s(statuses, {"L1-DASH-004"}) == []
    assert _M.uncovered_l1s(statuses, set()) == ["L1-DASH-004"]


@pytest.mark.requirement("L3-CICD-018")
def test_only_draft_counts_as_uncovered() -> None:
    """Implemented / Partially Implemented L1s are never reported as uncovered."""
    statuses = {"L1-A-001": "Implemented", "L1-B-002": "Partially Implemented"}
    assert _M.uncovered_l1s(statuses, set()) == []


@pytest.mark.requirement("L3-CICD-018")
def test_main_passes_against_the_repo() -> None:
    """End-to-end: the gate exits 0 against the committed matrix + allowlist."""
    assert _M.main() == _M.EXIT_OK


# -----------------------------------------------------------------------------
# L3-CICD-019 — allowlist format
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-019")
def test_parse_allowlist_returns_id_set() -> None:
    """A well-formed allowlist parses to its id set."""
    text = '[[allowed]]\nid = "L1-DASH-004"\nreason = "deferred, see R-DASH-004"\n'
    assert _M.parse_allowlist(text) == {"L1-DASH-004"}


@pytest.mark.requirement("L3-CICD-019")
def test_parse_allowlist_rejects_reasonless_entry() -> None:
    """An entry with a missing/empty reason is a parse failure."""
    with pytest.raises(_M.AllowlistError):
        _M.parse_allowlist('[[allowed]]\nid = "L1-DASH-004"\n')
    with pytest.raises(_M.AllowlistError):
        _M.parse_allowlist('[[allowed]]\nid = "L1-DASH-004"\nreason = "  "\n')


@pytest.mark.requirement("L3-CICD-019")
def test_parse_allowlist_rejects_malformed_id() -> None:
    """An id not matching L1-<CAT>-<NNN> is rejected."""
    with pytest.raises(_M.AllowlistError):
        _M.parse_allowlist('[[allowed]]\nid = "DASH-004"\nreason = "x"\n')


@pytest.mark.requirement("L3-CICD-019")
def test_committed_allowlist_parses() -> None:
    """The committed allowlist is well-formed.

    Empty since v0.12.0, except while a spec-first release is mid-flight: the
    v0.15.0 admin-console + login L1s are allowlisted between their spec landing
    and their implementation, and the list returns to empty at the release cut.
    """
    allowed = _M.parse_allowlist(_ALLOWLIST.read_text(encoding="utf-8"))
    assert allowed == {"L1-DASH-008"}
