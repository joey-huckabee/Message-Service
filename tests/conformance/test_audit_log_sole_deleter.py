"""Conformance test: the audit-log retention pruner is the sole deleter.

L3-OBS-039 says the audit-log retention pruner SHALL be the only
code path under ``src/`` that issues ``DELETE`` or ``UPDATE``
against the ``audit_log`` table. This preserves the L1-OBS-003
append-only invariant declared by L1-DASH-005's rationale ("the
audit log has neither an UPDATE nor a DELETE verb in any code
path other than the retention pruner").

This conformance test scans every ``*.py`` file under
``src/message_service/`` for SQL string literals containing
``DELETE FROM audit_log`` or ``UPDATE audit_log``, and asserts
that only the explicit allow-list of modules contains them.

Static-scan limitations:

* The scan is on string-literal content, so dynamically-built SQL
  (string concatenation, ``str.format``, f-strings with computed
  table names) would not be flagged. The codebase convention is
  module-level SQL constants with literal table names; future PRs
  introducing dynamic SQL against ``audit_log`` would need to be
  reviewed against L3-OBS-039 explicitly.
* The scan does not analyze who *calls* the SQL-bearing module —
  the contract holds because the SQL itself is gated by the
  allow-list, not by call chains.

Allow-listed modules (with rationale):

* ``application/use_cases/audit_log_pruner.py`` — the canonical
  caller. Per L3-OBS-040 this module also documents the
  anti-recursion rule (no audit row is emitted for the prune
  action itself).
* ``infrastructure/persistence/audit_log.py`` — the SQLite
  adapter that owns the ``DELETE`` SQL behind
  ``AuditLog.delete_older_than``. Only the pruner use case calls
  this method; the SQL is encapsulated inside the adapter as the
  natural consequence of the port-method abstraction. A separate
  test in this file asserts the pruner is the only caller of
  ``delete_older_than``.

Requirement references
----------------------
L1-OBS-003 (append-only audit log)
L1-DASH-005 (rationale: no UPDATE/DELETE outside the pruner)
L2-OBS-008 (retention enforcement)
L3-OBS-039 (sole-deleter conformance)
L3-OBS-040 (anti-recursion: no audit row for the prune action)
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "message_service"

# Modules permitted to contain SQL that mutates `audit_log`. Every other
# module SHALL contain only SELECT or INSERT against `audit_log`.
_ALLOWED_AUDIT_MUTATORS: frozenset[str] = frozenset(
    {
        "application/use_cases/audit_log_pruner.py",
        "infrastructure/persistence/audit_log.py",
    }
)

# Module permitted to call `delete_older_than` on the AuditLog port.
# (The adapter implements it; the use case is the only legitimate
# caller.)
_ALLOWED_DELETE_OLDER_THAN_CALLERS: frozenset[str] = frozenset(
    {
        "application/use_cases/audit_log_pruner.py",
    }
)

# Patterns that indicate a forbidden mutation of audit_log. Compiled with
# IGNORECASE so casing variations are caught. \s+ tolerates the
# multiline SQL formatting common in this codebase.
_FORBIDDEN_SQL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"DELETE\s+FROM\s+audit_log\b", re.IGNORECASE),
    re.compile(r"UPDATE\s+audit_log\b", re.IGNORECASE),
)


def _module_relpath(path: Path) -> str:
    """Return the path relative to ``_SRC`` using forward slashes."""
    return path.relative_to(_SRC).as_posix()


def _walk_python_files(root: Path) -> Iterable[Path]:
    """Yield every ``*.py`` file under ``root``."""
    yield from root.rglob("*.py")


def _string_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Extract every string literal in the AST, paired with its line number."""
    results: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            results.append((node.lineno, node.value))
    return results


def _find_audit_mutation_violations(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(lineno, snippet)`` for any string literal containing forbidden SQL."""
    violations: list[tuple[int, str]] = []
    for lineno, literal in _string_literals(tree):
        for pattern in _FORBIDDEN_SQL_PATTERNS:
            match = pattern.search(literal)
            if match is not None:
                violations.append((lineno, match.group(0)))
    return violations


def _find_delete_older_than_calls(tree: ast.AST) -> list[int]:
    """Return line numbers where ``X.delete_older_than(...)`` is called."""
    callsites: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "delete_older_than"
        ):
            callsites.append(node.lineno)
    return callsites


@pytest.mark.requirement("L3-OBS-039")
def test_only_allow_listed_modules_contain_audit_log_mutation_sql() -> None:
    """No src/ module outside the allow-list SHALL contain DELETE/UPDATE audit_log SQL."""
    offenders: list[str] = []
    for py_path in _walk_python_files(_SRC):
        relpath = _module_relpath(py_path)
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        violations = _find_audit_mutation_violations(tree)
        if not violations:
            continue
        if relpath in _ALLOWED_AUDIT_MUTATORS:
            continue
        for lineno, snippet in violations:
            offenders.append(f"{relpath}:{lineno} -- {snippet!r}")
    assert not offenders, (
        "L3-OBS-039 violation: SQL literals mutating audit_log in non-allow-listed "
        "src/ modules. If this SQL is justified (e.g., a new audited deletion path), "
        "update _ALLOWED_AUDIT_MUTATORS in this test with documented rationale.\n"
        + "\n".join(offenders)
    )


@pytest.mark.requirement("L3-OBS-039")
def test_only_pruner_module_calls_delete_older_than() -> None:
    """``AuditLog.delete_older_than(...)`` SHALL be called only by the pruner module.

    The SQL is encapsulated in the adapter, which would not by itself
    flag a renegade caller. This test scans the AST for any
    ``X.delete_older_than(...)`` call site and asserts it lives in the
    allow-list.
    """
    offenders: list[str] = []
    for py_path in _walk_python_files(_SRC):
        relpath = _module_relpath(py_path)
        tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        callsites = _find_delete_older_than_calls(tree)
        if not callsites:
            continue
        if relpath in _ALLOWED_DELETE_OLDER_THAN_CALLERS:
            continue
        for lineno in callsites:
            offenders.append(f"{relpath}:{lineno} -- delete_older_than(...)")
    assert not offenders, (
        "L3-OBS-039 violation: delete_older_than callers outside the allow-list. "
        "Only application/use_cases/audit_log_pruner.py is permitted to call this "
        "port method.\n" + "\n".join(offenders)
    )


@pytest.mark.requirement("L3-OBS-039")
def test_allow_list_modules_actually_exist() -> None:
    """Every allow-list path SHALL exist on disk to prevent silent drift."""
    for relpath in _ALLOWED_AUDIT_MUTATORS | _ALLOWED_DELETE_OLDER_THAN_CALLERS:
        full = _SRC / relpath
        assert full.exists(), (
            f"Allow-list contains {relpath!r} but {full} does not exist; "
            "the conformance test is masking a non-existent module."
        )


@pytest.mark.requirement("L3-OBS-039")
def test_audit_log_pruner_module_actually_calls_delete_older_than() -> None:
    """Negative-space companion: the pruner SHALL contain at least one call.

    If a refactor silently removes the pruner's only call site, the
    sole-deleter test would still pass vacuously while the L3-OBS-014..
    016 deletion machinery is broken. This test fails fast in that
    scenario.
    """
    pruner_path = _SRC / "application" / "use_cases" / "audit_log_pruner.py"
    tree = ast.parse(pruner_path.read_text(encoding="utf-8"), filename=str(pruner_path))
    callsites = _find_delete_older_than_calls(tree)
    assert callsites, (
        "audit_log_pruner.py is expected to call delete_older_than(); the "
        "L3-OBS-014..016 deletion machinery appears to be missing."
    )
