#!/usr/bin/env python3
"""Regenerate docs/TRACE-MATRIX.md from requirement sources and pytest markers.

This tool walks three sources and emits a single trace matrix document:

1. ``docs/L1-REQ.md`` — for L1 ids and their declared verification methods
2. ``docs/L2-REQ.md``, ``docs/L3-REQ.md`` — for L2/L3 ids with ``Parent:`` fields
3. ``tests/`` — for every ``@pytest.mark.requirement("L<N>-<CAT>-<NNN>")``
   marker, collected via AST parse

The output per requirement row includes:

* L2/L3 children (from parent fields)
* Test artifacts (from pytest markers) in pytest discovery format
* Status rolled up by :func:`compute_status` per the rule:

  - **Implemented** — every child is Implemented (or, for a leaf, the
    requirement has at least one direct verification artifact)
  - **Partially Implemented** — some children done, some not (or all
    children Draft but the parent itself has direct artifacts)
  - **Draft** — no verification artifact anywhere in the subtree
  - **Verified** — *(future)* every required Verification Method has an
    artifact

Status and verification-artifact fields used to live in the L1/L2/L3
source docs. Increment 25a removed them so this script is the sole
authority — the source docs hold pure spec content, this matrix holds
live status.

Run from the project root:

    poetry run python scripts/build-trace-matrix.py

Or via pre-commit / CI. The output overwrites ``docs/TRACE-MATRIX.md``.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
L1_DOC = ROOT / "docs" / "L1-REQ.md"
L2_DOC = ROOT / "docs" / "L2-REQ.md"
L3_DOC = ROOT / "docs" / "L3-REQ.md"
TRACE_DOC = ROOT / "docs" / "TRACE-MATRIX.md"
TESTS_DIR = ROOT / "tests"

REQ_ID_PATTERN = re.compile(r"L(?P<level>[123])-(?P<cat>[A-Z]+)-(?P<num>\d+)")
L2_HEADER = re.compile(r"^####\s+L2-([A-Z]+)-(\d+)\s*$", re.MULTILINE)
L3_LINE = re.compile(
    r"^\*\*L3-([A-Z]+)-(\d+)\*\*\s+·\s+Parent:\s+(L2-[A-Z]+-\d+)",
    re.MULTILINE,
)
L2_PARENT_LINE = re.compile(r"^\*\*Parent\*\*:\s+(L1-[A-Z]+-\d+)\s*$", re.MULTILINE)

# Categories in the canonical order used in L1-REQ.md
CATEGORIES: list[tuple[str, str]] = [
    ("API", "gRPC interface"),
    ("RUN", "Run lifecycle"),
    ("STAGE", "Stage lifecycle and idempotency"),
    ("TMPL", "Template governance and sandboxing"),
    ("AGGR", "Aggregation and composition"),
    ("SWEEP", "Orphan detection and disposition"),
    ("SUB", "Subscriptions and tags"),
    ("AUTH", "Authentication"),
    ("MAIL", "Email delivery"),
    ("DASH", "Dashboard"),
    ("PERS", "Persistence"),
    ("OBS", "Observability"),
    ("ERR", "Error handling and exception taxonomy"),
    ("CFG", "Configuration"),
    ("DEP", "Deployment"),
    ("CICD", "Continuous integration and delivery"),
]


def parse_l1_ids(doc: str) -> list[str]:
    """L1 ids appear as level-3 headers ``### L1-XXX-NNN`` in L1-REQ.md."""
    return re.findall(r"^###\s+(L1-[A-Z]+-\d+)\s*$", doc, re.MULTILINE)


def parse_l2_parent_map(doc: str) -> dict[str, str]:
    """Return mapping L2-id → L1-parent-id from L2-REQ.md."""
    result: dict[str, str] = {}
    # Split into per-L2 blocks at each #### L2-XXX-NNN header
    blocks = re.split(r"^####\s+(L2-[A-Z]+-\d+)\s*$", doc, flags=re.MULTILINE)
    # blocks[0] is preamble; pairs of (id, body) follow
    for i in range(1, len(blocks), 2):
        l2_id = blocks[i]
        body = blocks[i + 1] if i + 1 < len(blocks) else ""
        m = L2_PARENT_LINE.search(body)
        if m:
            result[l2_id] = m.group(1)
    return result


def parse_l3_parent_map(doc: str) -> dict[str, str]:
    """Return mapping L3-id → L2-parent-id from L3-REQ.md."""
    result: dict[str, str] = {}
    for match in L3_LINE.finditer(doc):
        cat, num, parent = match.groups()
        result[f"L3-{cat}-{num}"] = parent
    return result


def collect_test_markers(tests_dir: Path) -> dict[str, list[str]]:
    """Walk every ``.py`` file under tests/ and collect requirement markers.

    Returns a dict ``requirement_id -> list of test artifact paths`` in
    pytest discovery format (``path::function_name``).
    """
    marker_map: dict[str, list[str]] = defaultdict(list)

    for py_file in sorted(tests_dir.rglob("*.py")):
        if py_file.name == "__init__.py" or "conftest" in py_file.name:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for decorator in node.decorator_list:
                req_id = _extract_requirement_id(decorator)
                if req_id:
                    rel = py_file.relative_to(ROOT).as_posix()
                    marker_map[req_id].append(f"{rel}::{node.name}")
    return marker_map


def _extract_requirement_id(decorator: ast.expr) -> str | None:
    """Return the requirement id from a ``@pytest.mark.requirement("...")`` decorator."""
    # Case: @pytest.mark.requirement("L3-RUN-007")
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    # Walk back to "mark.requirement"
    if not (isinstance(func, ast.Attribute) and func.attr == "requirement"):
        return None
    if not decorator.args:
        return None
    first_arg = decorator.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value
    return None


def build_matrix() -> str:
    """Build the full trace-matrix markdown by walking req docs and test markers.

    Returns:
        The complete markdown document as a string, ready to write to
        ``docs/TRACE-MATRIX.md``.
    """
    l1_doc = L1_DOC.read_text(encoding="utf-8")
    l2_doc = L2_DOC.read_text(encoding="utf-8")
    l3_doc = L3_DOC.read_text(encoding="utf-8")

    l1_ids = parse_l1_ids(l1_doc)
    l2_parent = parse_l2_parent_map(l2_doc)
    l3_parent = parse_l3_parent_map(l3_doc)
    test_markers = collect_test_markers(TESTS_DIR)

    # Invert parent maps
    l1_to_l2: dict[str, list[str]] = defaultdict(list)
    for l2_id, l1_id in l2_parent.items():
        l1_to_l2[l1_id].append(l2_id)
    for l1_id in l1_to_l2:
        l1_to_l2[l1_id].sort(key=_sort_key)

    l2_to_l3: dict[str, list[str]] = defaultdict(list)
    for l3_id, l2_id in l3_parent.items():
        l2_to_l3[l2_id].append(l3_id)
    for l2_id in l2_to_l3:
        l2_to_l3[l2_id].sort(key=_sort_key)

    # Build the output
    lines: list[str] = []
    lines.append("# Message-Service — Requirements Trace Matrix")
    lines.append("")
    lines.append("<!-- AUTO-GENERATED by scripts/build-trace-matrix.py. Do not edit by hand. -->")
    lines.append("")
    lines.append("## Purpose")
    lines.append("")
    lines.append(
        "Forward trace from L1 through L2 and L3 to verification artifacts. "
        "This file is regenerated from `L1-REQ.md`, `L2-REQ.md`, `L3-REQ.md`, "
        "and the `@pytest.mark.requirement` markers in `tests/` each time "
        "`scripts/build-trace-matrix.py` is run."
    )
    lines.append("")
    lines.append("## Status rollup")
    lines.append("")
    lines.append("Status is computed by `scripts/build-trace-matrix.py`'s rollup rule.")
    lines.append("Source-doc `Status:` fields were removed in Increment 25a; this matrix is")
    lines.append("the single source of truth.")
    lines.append("")
    lines.append("* **Draft** — no verification artifact anywhere in the subtree.")
    lines.append(
        "* **Partially Implemented** — at least one child has artifacts but"
        " not all are Implemented; or the row itself has direct artifacts but"
        " its children include Drafts."
    )
    lines.append(
        "* **Implemented** — every child rolls up to Implemented (or, for a"
        " leaf, the row has at least one direct verification artifact)."
    )
    lines.append(
        "* **Verified** — *(future)* every required Verification Method"
        " category has at least one corresponding artifact."
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Per-category sections
    for cat_code, cat_title in CATEGORIES:
        cat_l1s = [req for req in l1_ids if req.startswith(f"L1-{cat_code}-")]
        if not cat_l1s:
            continue
        lines.append(f"### L1-{cat_code}: {cat_title}")
        lines.append("")

        # L1 → L2
        lines.append("**L1 → L2**")
        lines.append("")
        lines.append("| L1 ID | L2 Children | Status |")
        lines.append("|-------|-------------|--------|")
        for l1_id in cat_l1s:
            children = l1_to_l2.get(l1_id, [])
            children_str = ", ".join(children) if children else "_(none)_"
            child_statuses = [_l2_status(l2_id, l2_to_l3, test_markers) for l2_id in children]
            status = compute_status(
                has_direct_artifacts=bool(test_markers.get(l1_id)),
                children_statuses=child_statuses,
            )
            lines.append(f"| {l1_id} | {children_str} | {status} |")
        lines.append("")

        # L2 → L3 with verification artifacts
        lines.append("**L2 → L3 → Verification Artifacts**")
        lines.append("")
        lines.append("| L2 ID | L3 Children | Test Artifacts | Status |")
        lines.append("|-------|-------------|----------------|--------|")

        cat_l2s = sorted(
            [l2 for l2 in l2_parent if l2.startswith(f"L2-{cat_code}-")],
            key=_sort_key,
        )
        for l2_id in cat_l2s:
            l3_children = l2_to_l3.get(l2_id, [])
            # Aggregate test artifacts across all L3 children AND any markers
            # pointing directly at the L2 id, for the visible artifacts column.
            artifacts: list[str] = list(test_markers.get(l2_id, []))
            for l3_id in l3_children:
                artifacts.extend(test_markers.get(l3_id, []))
            artifacts = sorted(set(artifacts))

            children_str = ", ".join(l3_children) if l3_children else "_(none)_"
            artifacts_str = "<br>".join(f"`{a}`" for a in artifacts) if artifacts else "_(TBD)_"
            status = _l2_status(l2_id, l2_to_l3, test_markers)
            lines.append(f"| {l2_id} | {children_str} | {artifacts_str} | {status} |")
        lines.append("")

    # Coverage summary
    lines.append("---")
    lines.append("")
    lines.append("## Coverage summary")
    lines.append("")
    lines.append("| Category | L1 | L2 | L3 | L2s with tests | L3s with tests |")
    lines.append("|----------|----|----|-----|----------------|----------------|")
    total_l1 = total_l2 = total_l3 = 0
    total_l2_tested = total_l3_tested = 0
    for cat_code, _ in CATEGORIES:
        l1s = [req for req in l1_ids if req.startswith(f"L1-{cat_code}-")]
        l2s = [req for req in l2_parent if req.startswith(f"L2-{cat_code}-")]
        l3s = [req for req in l3_parent if req.startswith(f"L3-{cat_code}-")]
        l2_tested = sum(1 for l2 in l2s if test_markers.get(l2))
        l3_tested = sum(1 for l3 in l3s if test_markers.get(l3))
        lines.append(
            f"| {cat_code} | {len(l1s)} | {len(l2s)} | {len(l3s)} | {l2_tested} | {l3_tested} |"
        )
        total_l1 += len(l1s)
        total_l2 += len(l2s)
        total_l3 += len(l3s)
        total_l2_tested += l2_tested
        total_l3_tested += l3_tested
    lines.append(
        f"| **Total** | **{total_l1}** | **{total_l2}** | **{total_l3}** | "
        f"**{total_l2_tested}** | **{total_l3_tested}** |"
    )
    lines.append("")
    lines.append(
        f"**Requirements verified by at least one test**: "
        f"{total_l2_tested + total_l3_tested} of {total_l2 + total_l3} "
        f"({(total_l2_tested + total_l3_tested) * 100 / (total_l2 + total_l3):.1f}%)."
    )
    lines.append("")

    # Orphan check
    orphan_l2s = [l2 for l2 in l2_parent if l2_parent[l2] not in l1_ids]
    orphan_l3s = [l3 for l3 in l3_parent if l3_parent[l3] not in l2_parent]
    lines.append("### Orphan check")
    lines.append("")
    lines.append(f"* Orphan L2s (parent L1 not found): **{len(orphan_l2s)}**")
    lines.append(f"* Orphan L3s (parent L2 not found): **{len(orphan_l3s)}**")
    if orphan_l2s:
        lines.append("")
        lines.append("**Orphan L2s:**")
        for l2 in orphan_l2s:
            lines.append(f"* {l2} → parent {l2_parent[l2]} not in L1-REQ.md")
    if orphan_l3s:
        lines.append("")
        lines.append("**Orphan L3s:**")
        for l3 in orphan_l3s:
            lines.append(f"* {l3} → parent {l3_parent[l3]} not in L2-REQ.md")
    lines.append("")

    # Marker references to nonexistent reqs
    all_known = set(l1_ids) | set(l2_parent) | set(l3_parent)
    unknown_markers = sorted(set(test_markers) - all_known)
    lines.append("### Marker reference check")
    lines.append("")
    lines.append(f"* Markers referencing unknown requirement ids: **{len(unknown_markers)}**")
    if unknown_markers:
        lines.append("")
        for req_id in unknown_markers:
            count = len(test_markers[req_id])
            lines.append(f"* `{req_id}` — referenced by {count} test(s)")

    # Trailing "\n" + no final "" entry → output ends with exactly one
    # newline. The pre-commit end-of-file-fixer hook would otherwise
    # silently trim a double-newline tail, hiding the drift from
    # `--check`'s byte comparison (caught while implementing 26c).
    return "\n".join(lines) + "\n"


def _sort_key(req_id: str) -> tuple[str, int]:
    """Sort requirement ids by category then numeric suffix."""
    m = REQ_ID_PATTERN.search(req_id)
    if not m:
        return (req_id, 0)
    return (m.group("cat"), int(m.group("num")))


def compute_status(
    *,
    has_direct_artifacts: bool,
    children_statuses: list[str],
) -> str:
    """Roll up status for one requirement node (single source of truth).

    Used for both L1 (children = L2 statuses) and L2 (children = L3
    statuses) rows in the trace matrix. Leaf-level rows (L3 here, since
    we don't model individual verification methods yet) are computed
    by passing ``children_statuses=[]`` and the leaf's direct artifacts.

    Args:
        has_direct_artifacts: True if at least one test marker points
            directly at this requirement id.
        children_statuses: List of child requirement statuses, each one
            of ``{"Implemented", "Partially Implemented", "Draft"}``.
            Pass ``[]`` for leaf nodes.

    Returns:
        One of ``"Implemented"``, ``"Partially Implemented"``, or
        ``"Draft"``.

    Rules:

    * **Leaf** (no children): ``Implemented`` iff direct artifacts exist;
      otherwise ``Draft``.
    * **Parent**:

      - All children ``Implemented`` → ``Implemented``.
      - All children ``Draft`` AND no direct artifacts → ``Draft``.
      - Otherwise (mix of statuses, or all-Draft with direct
        artifacts, or any ``Partially Implemented`` child) →
        ``Partially Implemented``.
    """
    if not children_statuses:
        return "Implemented" if has_direct_artifacts else "Draft"

    n = len(children_statuses)
    impl_count = sum(1 for s in children_statuses if s == "Implemented")
    draft_count = sum(1 for s in children_statuses if s == "Draft")

    if impl_count == n:
        return "Implemented"
    if draft_count == n and not has_direct_artifacts:
        return "Draft"
    return "Partially Implemented"


def _l2_status(
    l2_id: str,
    l2_to_l3: dict[str, list[str]],
    test_markers: dict[str, list[str]],
) -> str:
    """Compute one L2's status by rolling up its L3 children + direct markers."""
    l3_children = l2_to_l3.get(l2_id, [])
    child_statuses = [
        compute_status(
            has_direct_artifacts=bool(test_markers.get(l3_id)),
            children_statuses=[],
        )
        for l3_id in l3_children
    ]
    return compute_status(
        has_direct_artifacts=bool(test_markers.get(l2_id)),
        children_statuses=child_statuses,
    )


# -----------------------------------------------------------------------------
# --check mode (Increment 26c): traceability gate for CI.
# Exit codes (per L3-CICD-011 / L3-CICD-012):
#   0 — clean (regenerated output matches committed; rollups consistent)
#   1 — byte-diff against committed file
#   2 — input doc parse failure (or committed matrix unreadable)
#   3 — rollup inconsistency in the committed matrix
# -----------------------------------------------------------------------------

# Status values the matrix may print. Order matters for the
# byte-diff regex below (longest first so "Partially Implemented"
# isn't shadowed by "Implemented").
_STATUS_RE = r"(?:Partially Implemented|Implemented|Draft|Verified)"

_L1_ROW_RE = re.compile(
    rf"^\|\s+(L1-[A-Z]+-\d+)\s+\|\s+([^|]*?)\s+\|\s+({_STATUS_RE})\s+\|\s*$",
    re.MULTILINE,
)

_L2_ROW_RE = re.compile(
    rf"^\|\s+(L2-[A-Z]+-\d+)\s+\|\s+([^|]*?)\s+\|\s+[^|]*?\s+\|\s+({_STATUS_RE})\s+\|\s*$",
    re.MULTILINE,
)


def _parse_committed_l1_rows(matrix_text: str) -> dict[str, tuple[list[str], str]]:
    """Parse L1 rows from a committed trace matrix.

    Returns:
        Mapping ``l1_id -> (l2_children, status)``. Children are the
        comma-separated ids in the "L2 Children" column; ``_(none)_``
        is normalized to an empty list.
    """
    result: dict[str, tuple[list[str], str]] = {}
    for match in _L1_ROW_RE.finditer(matrix_text):
        l1_id, children_str, status = match.groups()
        children: list[str]
        if children_str.strip() == "_(none)_":
            children = []
        else:
            children = [c.strip() for c in children_str.split(",") if c.strip()]
        result[l1_id] = (children, status)
    return result


def _parse_committed_l2_rows(matrix_text: str) -> dict[str, str]:
    """Parse L2 rows from a committed trace matrix.

    Returns:
        Mapping ``l2_id -> status``. The L3-children and artifacts
        columns are not returned — for the 26c rollup check they are
        not needed (L1 verification only reads L2 statuses).
    """
    result: dict[str, str] = {}
    for match in _L2_ROW_RE.finditer(matrix_text):
        l2_id, _children_str, status = match.groups()
        result[l2_id] = status
    return result


def verify_rollup_consistency(
    matrix_text: str,
    *,
    test_markers: dict[str, list[str]] | None = None,
    l2_to_l3: dict[str, list[str]] | None = None,
) -> list[str]:
    """Self-consistency check on the committed matrix.

    For every L1 and L2 row, verifies that the committed status agrees
    with what :func:`compute_status` produces under the Increment 25a
    propagation rule. Catches a hand-edited matrix or a matrix produced
    by an older script version whose rollup rule differed.

    The check needs ``test_markers`` to know whether each row has direct
    verification artifacts (markers tagged with the row's id directly),
    and it needs ``l2_to_l3`` to compute L3 children's statuses for the
    L2 rollup. Both default to a fresh re-collection from the live test
    tree and L3 source doc.

    Args:
        matrix_text: Contents of ``docs/TRACE-MATRIX.md``.
        test_markers: Optional pre-collected markers (used by tests
            to inject synthetic state). Defaults to a fresh
            :func:`collect_test_markers` pass.
        l2_to_l3: Optional pre-built L2→L3 children map. Defaults to a
            fresh parse of L3-REQ.md.

    Returns:
        List of human-readable violation descriptions, sorted by row
        id. Empty list means the matrix is internally consistent.
    """
    if test_markers is None:
        test_markers = collect_test_markers(TESTS_DIR)
    if l2_to_l3 is None:
        l3_doc = L3_DOC.read_text(encoding="utf-8")
        l3_parent = parse_l3_parent_map(l3_doc)
        l2_to_l3_built: dict[str, list[str]] = defaultdict(list)
        for l3_id, l2_id in l3_parent.items():
            l2_to_l3_built[l2_id].append(l3_id)
        l2_to_l3 = l2_to_l3_built

    l1_rows = _parse_committed_l1_rows(matrix_text)
    l2_rows = _parse_committed_l2_rows(matrix_text)

    violations: list[str] = []

    # L2 rows: rebuild expected status from L3 children + direct markers.
    for l2_id, committed_status in sorted(l2_rows.items()):
        l3_children = l2_to_l3.get(l2_id, [])
        l3_statuses = [
            compute_status(
                has_direct_artifacts=bool(test_markers.get(l3_id)),
                children_statuses=[],
            )
            for l3_id in l3_children
        ]
        expected = compute_status(
            has_direct_artifacts=bool(test_markers.get(l2_id)),
            children_statuses=l3_statuses,
        )
        if expected != committed_status:
            violations.append(
                f"{l2_id}: committed status '{committed_status}' but "
                f"L3 children {l3_children} → statuses {l3_statuses} → "
                f"expected '{expected}' under the propagation rule (Increment 25a)"
            )

    # L1 rows: rebuild expected status from L2 children's committed
    # statuses + direct markers on the L1 id.
    for l1_id, (children, committed_status) in sorted(l1_rows.items()):
        l2_statuses = [l2_rows[c] for c in children if c in l2_rows]
        # Skip rows whose children we can't resolve — that's an
        # orphan-trace problem, surfaced separately by the matrix's
        # "Orphan check" section. Don't double-fail.
        if len(l2_statuses) != len(children):
            continue
        expected = compute_status(
            has_direct_artifacts=bool(test_markers.get(l1_id)),
            children_statuses=l2_statuses,
        )
        if expected != committed_status:
            violations.append(
                f"{l1_id}: committed status '{committed_status}' but L2 children "
                f"{children} → statuses {l2_statuses} → expected '{expected}' "
                f"under the propagation rule (Increment 25a)"
            )
    return violations


def _check_main() -> int:
    """``--check`` mode body. See exit-code table at the top of this section."""
    # Step 1: regenerate in memory. Catches input-doc parse failures.
    try:
        regenerated = build_matrix()
    except Exception as exc:
        print(
            f"error: failed to parse requirement source docs: {exc}",
            file=sys.stderr,
        )
        return 2

    # Step 2: read committed matrix. Catches missing/unreadable file.
    try:
        committed = TRACE_DOC.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(
            f"error: {TRACE_DOC.relative_to(ROOT)} does not exist",
            file=sys.stderr,
        )
        return 2

    # Step 3: byte-diff. The common drift mode (forgot to regen) lands here.
    if regenerated != committed:
        print(
            "error: TRACE-MATRIX.md is out of sync with the regenerated output.",
            file=sys.stderr,
        )
        diff = difflib.unified_diff(
            committed.splitlines(keepends=True),
            regenerated.splitlines(keepends=True),
            fromfile="committed/docs/TRACE-MATRIX.md",
            tofile="regenerated",
            n=3,
        )
        sys.stderr.writelines(diff)
        print(
            "\nfix: run 'poetry run python scripts/build-trace-matrix.py' and commit.",
            file=sys.stderr,
        )
        return 1

    # Step 4: independent rollup audit on the committed file. Catches a
    # script bug or hand-edited matrix where the propagation rule has
    # been violated even though byte-diff is clean.
    violations = verify_rollup_consistency(committed)
    if violations:
        print(
            "error: trace matrix has rollup inconsistencies (Increment 25a propagation rule):",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 3

    print(f"trace matrix OK ({len(regenerated.splitlines())} lines)")
    return 0


def main() -> int:
    """CLI entry point.

    Default mode: regenerate ``docs/TRACE-MATRIX.md`` from the source
    docs + test markers. ``--check`` mode: gate for CI; see exit-code
    table at the top of this section.
    """
    parser = argparse.ArgumentParser(
        prog="build-trace-matrix",
        description=(
            "Regenerate the requirements trace matrix from the L1/L2/L3 "
            "source docs and pytest markers. With --check, verify the "
            "committed matrix is up to date and internally consistent "
            "without writing changes (used by CI per L1-CICD-004)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Verification mode: do not write. Exit 0 if the committed "
            "matrix matches the regenerated output AND the propagation "
            "rule holds; 1 on byte-diff; 2 on input-doc parse failure; "
            "3 on rollup inconsistency."
        ),
    )
    args = parser.parse_args()

    if args.check:
        return _check_main()

    output = build_matrix()
    # newline="\n" forces LF on every platform; the repo standard is LF
    # (enforced by the mixed-line-ending pre-commit hook).
    TRACE_DOC.write_text(output, encoding="utf-8", newline="\n")
    print(f"Wrote {TRACE_DOC.relative_to(ROOT)} ({len(output.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
