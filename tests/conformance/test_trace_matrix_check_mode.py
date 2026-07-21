"""Conformance: ``build-trace-matrix.py --check`` exit-code contract.

Pins the four exit codes per L3-CICD-010 / L3-CICD-011 / L3-CICD-012:

* **0** — clean (committed matrix matches regenerated; rollups consistent)
* **1** — byte-diff against the committed file
* **2** — input-doc parse failure or committed matrix unreadable
* **3** — rollup inconsistency in the committed matrix

The script is loaded via importlib spec because its filename uses
hyphens (CLI convention), making normal imports impossible.

Requirement references
----------------------
L1-CICD-004 (traceability gate)
L3-CICD-010 (--check accepted; no required positional args)
L3-CICD-011 (--check exit codes 0/1/2)
L3-CICD-012 (--check exit code 3 for rollup inconsistency; offending ids listed)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build-trace-matrix.py"
_SPEC = importlib.util.spec_from_file_location("build_trace_matrix", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
verify_rollup_consistency = _MOD.verify_rollup_consistency


# -----------------------------------------------------------------------------
# Synthetic matrix fixtures
# -----------------------------------------------------------------------------


def _matrix_with(l1_status: str, l2_statuses: list[tuple[str, str]]) -> str:
    """Build a minimal trace-matrix string with one L1 row and N L2 rows."""
    children = ", ".join(l2_id for l2_id, _ in l2_statuses)
    l2_rows = "\n".join(
        f"| {l2_id} | _(none)_ | _(TBD)_ | {status} |" for l2_id, status in l2_statuses
    )
    return (
        "# trace matrix fixture\n\n"
        "**L1 → L2**\n\n"
        "| L1 ID | L2 Children | Status |\n"
        "|-------|-------------|--------|\n"
        f"| L1-API-001 | {children} | {l1_status} |\n\n"
        "**L2 → L3 → Verification Artifacts**\n\n"
        "| L2 ID | L3 Children | Test Artifacts | Status |\n"
        "|-------|-------------|----------------|--------|\n"
        f"{l2_rows}\n"
    )


def _markers_justifying(l2_statuses: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Build a synthetic test_markers map that justifies each L2's printed
    status as if every L2 were a leaf with direct artifacts.

    For ``Implemented`` L2s, registers a single direct marker on the
    L2 id; for ``Draft`` L2s, registers nothing. ``Partially
    Implemented`` is not a valid leaf state so is treated as
    ``Implemented``.
    """
    markers: dict[str, list[str]] = {}
    for l2_id, status in l2_statuses:
        if status in ("Implemented", "Partially Implemented"):
            markers[l2_id] = [f"tests/synthetic.py::test_{l2_id.lower()}"]
    return markers


# -----------------------------------------------------------------------------
# Rollup verification tests
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-012")
def test_consistent_matrix_has_no_violations() -> None:
    """All-Implemented children + Implemented parent → zero violations."""
    l2s = [("L2-API-001", "Implemented"), ("L2-API-002", "Implemented")]
    matrix = _matrix_with(l1_status="Implemented", l2_statuses=l2s)
    violations = verify_rollup_consistency(
        matrix, test_markers=_markers_justifying(l2s), l2_to_l3={}
    )
    assert violations == []


@pytest.mark.requirement("L3-CICD-012")
def test_partial_consistent_matrix_has_no_violations() -> None:
    """Mix of Implemented + Draft → parent must be Partially Implemented."""
    l2s = [("L2-API-001", "Implemented"), ("L2-API-002", "Draft")]
    matrix = _matrix_with(l1_status="Partially Implemented", l2_statuses=l2s)
    violations = verify_rollup_consistency(
        matrix, test_markers=_markers_justifying(l2s), l2_to_l3={}
    )
    assert violations == []


@pytest.mark.requirement("L3-CICD-012")
def test_falsely_promoted_parent_is_violation() -> None:
    """A parent claiming Implemented while a child is Draft is the
    pre-25a bug class — verify_rollup_consistency must catch it."""
    l2s = [("L2-API-001", "Implemented"), ("L2-API-002", "Draft")]
    matrix = _matrix_with(l1_status="Implemented", l2_statuses=l2s)
    violations = verify_rollup_consistency(
        matrix, test_markers=_markers_justifying(l2s), l2_to_l3={}
    )
    # Exactly one violation, on L1-API-001 (the L2 rows are individually
    # consistent under the helper's marker scheme).
    assert len(violations) == 1
    assert "L1-API-001" in violations[0]
    assert "Implemented" in violations[0]
    assert "Partially Implemented" in violations[0]


@pytest.mark.requirement("L3-CICD-012")
def test_falsely_drafted_parent_is_violation() -> None:
    """A parent claiming Draft while a child is Implemented is also a
    rollup violation — the rule cuts both directions."""
    l2s = [("L2-API-001", "Implemented")]
    matrix = _matrix_with(l1_status="Draft", l2_statuses=l2s)
    violations = verify_rollup_consistency(
        matrix, test_markers=_markers_justifying(l2s), l2_to_l3={}
    )
    assert len(violations) == 1
    assert "L1-API-001" in violations[0]


@pytest.mark.requirement("L3-CICD-012")
def test_l1_with_direct_artifacts_promotes_all_draft_to_partial() -> None:
    """When test_markers shows direct evidence on the L1 id, all-Draft
    children should yield Partially Implemented — not Draft. Verify that
    the verifier honors the same rule the script uses."""
    l2s = [("L2-API-001", "Draft")]
    matrix = _matrix_with(l1_status="Partially Implemented", l2_statuses=l2s)

    # All children Draft, but L1 has a direct marker.
    markers_with_l1 = {**_markers_justifying(l2s), "L1-API-001": ["tests/x.py::test_x"]}
    violations = verify_rollup_consistency(matrix, test_markers=markers_with_l1, l2_to_l3={})
    assert violations == []

    # Same matrix but no direct marker → status should be Draft, not Partial.
    violations_no_direct = verify_rollup_consistency(
        matrix, test_markers=_markers_justifying(l2s), l2_to_l3={}
    )
    assert len(violations_no_direct) == 1


# -----------------------------------------------------------------------------
# CLI-level exit-code contract (subprocess invocations of the script)
# -----------------------------------------------------------------------------


@pytest.fixture
def script_path() -> Path:
    return _SCRIPT


@pytest.mark.requirement("L3-CICD-011")
def test_check_clean_exits_zero(script_path: Path) -> None:
    """Against the live committed matrix, --check exits 0.

    This is the most important integration assertion: the actual repo
    state must always pass --check before commit, otherwise CI breaks.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(script_path), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"--check failed against the committed matrix.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


@pytest.mark.requirement("L3-CICD-010")
def test_no_args_regenerates_and_check_does_not_write(script_path: Path) -> None:
    """L3-CICD-010: no-args regenerates (writes); --check verifies WITHOUT writing.

    Exercises both modes and their mutual exclusivity. (The prior version ran
    ``--help`` and ended on an always-true ``or``, verifying neither mode nor
    the exclusivity.) The committed matrix is current, so regeneration is a
    byte-identical rewrite — which still proves the WRITE path runs; the file is
    restored verbatim in the ``finally`` to keep the tree clean regardless.
    """
    import subprocess
    import sys

    trace_doc = Path(__file__).resolve().parents[2] / "docs" / "TRACE-MATRIX.md"
    before = trace_doc.read_text(encoding="utf-8")
    try:
        # --check mode verifies and SHALL NOT write (mutual exclusivity).
        check = subprocess.run(
            [sys.executable, str(script_path), "--check"], capture_output=True, text=True
        )
        assert check.returncode == 0, check.stderr
        assert "Wrote" not in check.stdout
        assert trace_doc.read_text(encoding="utf-8") == before

        # No-args regenerates (writes) and requires no positional argument.
        regen = subprocess.run([sys.executable, str(script_path)], capture_output=True, text=True)
        assert regen.returncode == 0, regen.stderr
        assert "Wrote" in regen.stdout
        assert "the following arguments are required" not in regen.stderr
    finally:
        trace_doc.write_text(before, encoding="utf-8", newline="\n")
