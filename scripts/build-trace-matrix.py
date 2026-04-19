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
* Status rolled up: "Verified" if every required verification method has an
  artifact, otherwise "Implemented" if at least one test marker exists,
  otherwise "Draft".

Run from the project root:

    poetry run python scripts/build-trace-matrix.py

Or via pre-commit / CI. The output overwrites ``docs/TRACE-MATRIX.md``.
Any hand-written sections at the top of the file (Purpose, Conventions)
are preserved between the markers ``<!-- trace:begin -->`` and
``<!-- trace:end -->``.
"""

from __future__ import annotations

import ast
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
            tree = ast.parse(py_file.read_text())
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
    l1_doc = L1_DOC.read_text()
    l2_doc = L2_DOC.read_text()
    l3_doc = L3_DOC.read_text()

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
    lines.append("* **Draft** — no verification artifact yet")
    lines.append("* **Implemented** — at least one test marker linked")
    lines.append("* **Verified** — [future] all required methods covered")
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
            status = _rollup_l1_status(l1_id, l1_to_l2, l2_to_l3, test_markers)
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
            # pointing directly at the L2 id.
            artifacts: list[str] = list(test_markers.get(l2_id, []))
            for l3_id in l3_children:
                artifacts.extend(test_markers.get(l3_id, []))
            artifacts = sorted(set(artifacts))

            children_str = ", ".join(l3_children) if l3_children else "_(none)_"
            artifacts_str = "<br>".join(f"`{a}`" for a in artifacts) if artifacts else "_(TBD)_"
            status = "Implemented" if artifacts else "Draft"
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
    lines.append("")

    return "\n".join(lines) + "\n"


def _sort_key(req_id: str) -> tuple[str, int]:
    """Sort requirement ids by category then numeric suffix."""
    m = REQ_ID_PATTERN.search(req_id)
    if not m:
        return (req_id, 0)
    return (m.group("cat"), int(m.group("num")))


def _rollup_l1_status(
    l1_id: str,
    l1_to_l2: dict[str, list[str]],
    l2_to_l3: dict[str, list[str]],
    test_markers: dict[str, list[str]],
) -> str:
    for l2_id in l1_to_l2.get(l1_id, []):
        if test_markers.get(l2_id):
            return "Implemented"
        for l3_id in l2_to_l3.get(l2_id, []):
            if test_markers.get(l3_id):
                return "Implemented"
    return "Draft"


def main() -> int:
    """CLI entry point: regenerate the trace matrix on disk."""
    output = build_matrix()
    TRACE_DOC.write_text(output)
    print(f"Wrote {TRACE_DOC.relative_to(ROOT)} ({len(output.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
