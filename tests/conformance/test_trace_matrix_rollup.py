"""Conformance: trace-matrix rollup propagation.

Pins the L1↔L2↔L3 status propagation rule introduced in Increment 25a:

* Leaf (no children): ``Implemented`` iff direct artifacts exist; else ``Draft``.
* Parent: all children ``Implemented`` → ``Implemented``; all children
  ``Draft`` and no direct artifacts → ``Draft``; otherwise →
  ``Partially Implemented``.

The script ``scripts/build-trace-matrix.py`` is not a Python module
(it's a hyphen-named CLI tool), so the helper is loaded via importlib
spec rather than a regular import.

These tests verify the rollup-propagation rule that the ``--check``
mode's exit-code 3 path enforces (``L3-CICD-012``). They exercise the
underlying ``compute_status`` function directly; the CLI surface is
covered in ``test_trace_matrix_check_mode.py``.

Requirement references
----------------------
L1-CICD-004 (traceability gate)
L3-CICD-012 (rollup-consistency check; exit code 3)
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
compute_status = _MOD.compute_status


# -----------------------------------------------------------------------------
# Leaf-level
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-012")
def test_leaf_with_artifacts_is_implemented() -> None:
    assert compute_status(has_direct_artifacts=True, children_statuses=[]) == "Implemented"


@pytest.mark.requirement("L3-CICD-012")
def test_leaf_without_artifacts_is_draft() -> None:
    assert compute_status(has_direct_artifacts=False, children_statuses=[]) == "Draft"


# -----------------------------------------------------------------------------
# Parent rollup — all-or-nothing children
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_all_children_implemented_is_implemented() -> None:
    assert (
        compute_status(
            has_direct_artifacts=False,
            children_statuses=["Implemented", "Implemented", "Implemented"],
        )
        == "Implemented"
    )


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_all_children_draft_and_no_direct_is_draft() -> None:
    assert (
        compute_status(
            has_direct_artifacts=False,
            children_statuses=["Draft", "Draft", "Draft"],
        )
        == "Draft"
    )


# -----------------------------------------------------------------------------
# Parent rollup — Partially Implemented cases
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_mix_of_impl_and_draft_is_partial() -> None:
    """The team-flagged case: today's matrix shows L1-SWEEP-001 as
    Implemented while L2-SWEEP-001 is Draft. Under the new rule the
    parent is Partially Implemented, not Implemented."""
    assert (
        compute_status(
            has_direct_artifacts=False,
            children_statuses=["Implemented", "Draft"],
        )
        == "Partially Implemented"
    )


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_partial_child_is_partial() -> None:
    """A Partially Implemented child propagates upward — the parent
    cannot be Implemented unless every descendant is."""
    assert (
        compute_status(
            has_direct_artifacts=False,
            children_statuses=["Implemented", "Partially Implemented"],
        )
        == "Partially Implemented"
    )


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_all_draft_but_direct_artifacts_is_partial() -> None:
    """Direct artifacts on the parent count as evidence even when
    children are all Draft — flag the row as Partially Implemented so
    the asymmetry surfaces during review."""
    assert (
        compute_status(
            has_direct_artifacts=True,
            children_statuses=["Draft", "Draft"],
        )
        == "Partially Implemented"
    )


# -----------------------------------------------------------------------------
# The historical bug (the team finding that motivated 25a)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-012")
def test_parent_with_one_child_implemented_does_not_falsely_promote() -> None:
    """Pre-25a behavior: a single Implemented child made the parent
    Implemented, hiding all the Draft siblings. The rule must reject
    this — a single Implemented child should yield Partially
    Implemented, not Implemented."""
    statuses = ["Implemented"] + ["Draft"] * 9
    assert (
        compute_status(has_direct_artifacts=False, children_statuses=statuses)
        == "Partially Implemented"
    )
