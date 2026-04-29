"""Conformance test for L3-RUN-031: Clock port is the wall-clock chokepoint.

AST-scans every module under `src/message_service/` and asserts the
only modules that call `datetime.now`, `datetime.utcnow`, or
`time.time` are the two permitted exceptions:

1. ``infrastructure/time/system_clock.py`` — the `Clock` port's
   production adapter (which MUST call `datetime.now(tz=UTC)` to
   satisfy the contract).
2. ``infrastructure/persistence/migration_runner.py`` — records
   `_migrations.applied_at` at startup migration time, which runs
   before the `Clock` port is constructed.

A new direct call elsewhere fails the build, ensuring future
maintainers route through the injected `Clock`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _PROJECT_ROOT / "src" / "message_service"

# Modules permitted to call wall-clock primitives directly.
_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "src/message_service/infrastructure/time/system_clock.py",
        "src/message_service/infrastructure/persistence/migration_runner.py",
    }
)

# Functions that read the host wall-clock.
_FORBIDDEN_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
    }
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.name != "__init__.py")


def _calls_in_module(path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, "module.attr") for every forbidden call site."""
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not isinstance(target, ast.Attribute):
            continue
        attr = target.attr
        value = target.value
        if not isinstance(value, ast.Name):
            continue
        module_name = value.id
        if (module_name, attr) in _FORBIDDEN_CALLS:
            found.append((node.lineno, f"{module_name}.{attr}"))
    return found


@pytest.mark.requirement("L3-RUN-031")
def test_clock_chokepoint_only_allowed_modules_call_wall_clock_primitives() -> None:
    """L3-RUN-031: only the two permitted modules SHALL call
    ``datetime.now`` / ``datetime.utcnow`` / ``time.time``.

    Any other source file that reads the host wall-clock directly is a
    chokepoint violation and SHALL fail the build until reworked to
    consume the injected `Clock` port.
    """
    violations: list[str] = []
    for path in _iter_python_files(_SRC_DIR):
        relative = path.relative_to(_PROJECT_ROOT).as_posix()
        if relative in _ALLOWED_MODULES:
            continue
        for lineno, call in _calls_in_module(path):
            violations.append(f"{relative}:{lineno}: forbidden call {call}(...)")

    assert not violations, (
        "Forbidden direct wall-clock calls (L3-RUN-031). "
        "Route through the injected `Clock` port instead:\n" + "\n".join(violations)
    )


@pytest.mark.requirement("L3-RUN-031")
def test_clock_chokepoint_allowed_modules_actually_exist() -> None:
    """L3-RUN-031: the allow-list refers to real files; if a file is
    deleted the allow-list entry SHALL be removed in the same commit.
    """
    for relative in _ALLOWED_MODULES:
        path = _PROJECT_ROOT / relative
        assert path.is_file(), (
            f"Allow-list points at non-existent file: {relative} "
            "(remove it from _ALLOWED_MODULES if no longer needed)"
        )
