"""Conformance test: the report pruner is the sole deleter of report files.

L3-PERS-035 says the report pruner SHALL be the only code path under
``src/`` that calls ``Path.unlink()``, ``Path.rmdir()``,
``shutil.rmtree()``, or ``os.remove()`` against persisted report
files under ``persistence.filesystem.report_directory``. The
bootstrap writable-test probe (``bootstrap/service.py``) is a
permitted exception: it creates and immediately deletes a
``.write_probe`` file as part of the L3-PERS-011 writable-test;
it never touches a report file produced by the ``ReportStore``
adapter.

This conformance test AST-scans every module under
``src/message_service/`` and asserts no module outside the explicit
allow-list invokes any of the four delete primitives. A static
scan cannot prove that the calls inside the allow-listed modules
target only report-directory paths (paths are runtime values), but
the surrounding module-level discipline carries the contract:

* ``application/use_cases/report_pruner.py`` exists for exactly
  this purpose; every ``unlink`` / ``rmdir`` call inside it is the
  documented L3-PERS-031 walk.
* ``bootstrap/service.py::_ensure_report_directory`` performs one
  ``probe.unlink(missing_ok=True)`` against a freshly-written
  ``.write_probe`` sentinel inside the same function; no other
  delete call appears in the bootstrap module.

Adding a new caller of any delete primitive against the
report-store root SHALL therefore either (a) live inside the
report pruner module and be audited, or (b) require an explicit
update to this allow-list with documented justification.

Requirement references
----------------------
L1-PERS-004 (rendered-report retention pruner)
L2-PERS-013 (PRUNE_REPORT audit + failure isolation)
L3-PERS-035 (sole-deleter conformance)
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "message_service"

# Module paths (relative to _SRC) that are permitted to call the
# filesystem-deletion primitives. Any other caller raises this
# conformance test.
_ALLOWED_DELETERS: frozenset[str] = frozenset(
    {
        # The report pruner — the canonical caller (L3-PERS-031).
        "application/use_cases/report_pruner.py",
        # The bootstrap writable-test probe — single .unlink() call
        # against the .write_probe sentinel created and removed in
        # the same _ensure_report_directory function (L3-PERS-011).
        "bootstrap/service.py",
    }
)

# Method names that, when called as ``X.method(...)``, are
# filesystem-deletion primitives. Matched syntactically — we don't
# resolve ``X`` to a type. This is conservative-by-design: a few
# false positives (e.g., calling ``.unlink()`` on a non-Path object)
# are acceptable; false negatives would defeat the L3-PERS-035
# guarantee.
_DELETE_METHODS: frozenset[str] = frozenset({"unlink", "rmdir"})

# Top-level call patterns: ``shutil.rmtree(...)``, ``os.remove(...)``,
# ``os.unlink(...)``. Match on the dotted attribute name only.
_DELETE_DOTTED_CALLS: frozenset[str] = frozenset(
    {
        "shutil.rmtree",
        "os.remove",
        "os.unlink",
    }
)


def _module_relpath(path: Path) -> str:
    """Return the path relative to ``_SRC`` using forward slashes."""
    return path.relative_to(_SRC).as_posix()


def _is_allowed(relpath: str) -> bool:
    return relpath in _ALLOWED_DELETERS


def _walk_python_files(root: Path) -> Iterable[Path]:
    """Yield every ``*.py`` file under ``root``."""
    yield from root.rglob("*.py")


def _dotted_name(node: ast.expr) -> str | None:
    """Return ``"a.b.c"`` for ``Attribute(Attribute(Name(a), b), c)``; else None."""
    parts: list[str] = []
    current: ast.expr | None = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _find_violations(tree: ast.AST) -> list[tuple[int, str]]:
    """Return list of ``(lineno, call-text)`` for forbidden delete calls.

    Detected patterns:
      * ``X.unlink(...)`` / ``X.rmdir(...)`` (any ``X``).
      * ``shutil.rmtree(...)`` / ``os.remove(...)`` / ``os.unlink(...)``.
    """
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute):
            # ``X.method(...)``. Two cases.
            # Case 1: deletion method (unlink/rmdir) on any X.
            if func.attr in _DELETE_METHODS:
                violations.append((node.lineno, f"<expr>.{func.attr}(...)"))
                continue
            # Case 2: dotted top-level (e.g., shutil.rmtree, os.remove).
            dotted = _dotted_name(func)
            if dotted in _DELETE_DOTTED_CALLS:
                violations.append((node.lineno, f"{dotted}(...)"))
    return violations


@pytest.mark.requirement("L3-PERS-035")
def test_only_allow_listed_modules_call_filesystem_delete_primitives() -> None:
    """No src/ module outside the allow-list SHALL call unlink/rmdir/rmtree/remove."""
    offenders: list[str] = []
    for py_path in _walk_python_files(_SRC):
        relpath = _module_relpath(py_path)
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        violations = _find_violations(tree)
        if not violations:
            continue
        if _is_allowed(relpath):
            continue
        for lineno, snippet in violations:
            offenders.append(f"{relpath}:{lineno} -- {snippet}")
    assert not offenders, (
        "L3-PERS-035 violation: filesystem-deletion calls in non-allow-listed src/ modules. "
        "If this call is justified (e.g., a new audited deletion path), update "
        "_ALLOWED_DELETERS in this test with documented rationale.\n" + "\n".join(offenders)
    )


@pytest.mark.requirement("L3-PERS-035")
def test_allow_list_modules_actually_exist() -> None:
    """Every module in ``_ALLOWED_DELETERS`` SHALL exist on disk.

    Defends the conformance test against silent drift if a module is
    renamed or moved; without this check the allow-list would shield
    a non-existent path.
    """
    for relpath in _ALLOWED_DELETERS:
        full = _SRC / relpath
        assert full.exists(), (
            f"_ALLOWED_DELETERS contains {relpath!r} but {full} does not exist; "
            "the conformance test is masking a non-existent module."
        )


@pytest.mark.requirement("L3-PERS-035")
def test_report_pruner_module_actually_calls_delete_primitives() -> None:
    """The report pruner SHALL contain at least one delete-primitive call.

    A negative-space companion to the sole-deleter test: if the
    pruner's calls are silently removed (e.g., a refactor that no
    longer evicts), the L3-PERS-031 contract is broken even though
    the sole-deleter test would still pass vacuously. This test
    fails fast if the pruner's deletion machinery disappears.
    """
    pruner_path = _SRC / "application" / "use_cases" / "report_pruner.py"
    tree = ast.parse(pruner_path.read_text(encoding="utf-8"), filename=str(pruner_path))
    violations = _find_violations(tree)  # "violations" only in the
    #                                      sole-deleter sense — here we
    #                                      EXPECT them.
    delete_calls = [snippet for _, snippet in violations]
    assert any("unlink" in c for c in delete_calls), (
        "report_pruner.py is expected to call .unlink(); the L3-PERS-031 "
        "deletion machinery appears to be missing."
    )
    assert any("rmdir" in c for c in delete_calls), (
        "report_pruner.py is expected to call .rmdir() in its cleanup walk; "
        "the L3-PERS-031 directory housekeeping appears to be missing."
    )
