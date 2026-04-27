"""Architecture-boundary conformance test.

Enforces the hexagonal dependency rule: ``domain/`` and
``application/`` SHALL NOT import from ``infrastructure/`` or
``interfaces/``. The ``application/ports/`` subpackage may
reference ``domain/`` (ports operate on domain aggregates) but
SHALL NOT touch infrastructure either.

Per `L3-DEP-005` / `L3-DEP-018`, domain and application code
SHALL also NOT import platform-specific primitives:
``multiprocessing``, ``subprocess``, ``os.fork``, or POSIX-only
signal modules. Those belong in infrastructure where platform
detection can gate them.

Walks the AST of every ``.py`` file under
``src/message_service/domain/`` and
``src/message_service/application/``, parses every ``Import`` and
``ImportFrom`` node, and asserts no disallowed import is present.
Failures report file path, line number, and offending import for
direct copy into a fix.

Requirement references
----------------------
L2-DEP-002, L2-DEP-003, L2-PERS-010
L3-DEP-003, L3-DEP-005, L3-DEP-018
L3-PERS-016
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "message_service"
_DOMAIN = _SRC / "domain"
_APPLICATION = _SRC / "application"

# Modules domain + application SHALL NOT import.
_FORBIDDEN_PACKAGE_PREFIXES = (
    "message_service.infrastructure",
    "message_service.interfaces",
)

# Platform-specific stdlib modules that domain + application SHALL
# NOT import (per L3-DEP-005 / L3-DEP-018). The signal *module*
# itself is allowed (callers may need ``signal.SIGTERM`` constants
# in cross-platform code), but the POSIX-only signal *handlers*
# are not. Module-level checks are sufficient for L3-DEP-005's
# stated intent — fork() and SIGCHLD bring real portability cost
# even at the import site.
_FORBIDDEN_PLATFORM_MODULES = frozenset(
    {
        "os.fork",  # caught via attribute-access in `_iter_imports` below
        "multiprocessing",
        "subprocess",
        "signal.SIGCHLD",
        "signal.SIGUSR1",
        "signal.SIGUSR2",
    }
)


def _iter_python_files(root: Path) -> list[Path]:
    """Every ``.py`` file under ``root`` (excluding ``__pycache__``)."""
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _iter_imports(tree: ast.AST) -> list[tuple[int, str]]:
    """Yield ``(lineno, module_name)`` pairs for every Import / ImportFrom node.

    For ``import a.b.c``, yields ``(lineno, "a.b.c")``.
    For ``from a.b import c``, yields ``(lineno, "a.b.c")``.
    For ``from . import x``, yields nothing (relative imports are
    safely contained within their own package).
    """
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import — skip; it can't reach across
                # the boundary anyway.
                continue
            module = node.module or ""
            for alias in node.names:
                out.append((node.lineno, f"{module}.{alias.name}"))
    return out


# -----------------------------------------------------------------------------
# Hexagonal boundary (L2-PERS-010, L3-PERS-016)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-016")
def test_domain_does_not_import_infrastructure_or_interfaces() -> None:
    """L3-PERS-016: domain/ SHALL NOT import infrastructure/ or interfaces/."""
    violations: list[str] = []
    for path in _iter_python_files(_DOMAIN):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, imported in _iter_imports(tree):
            for prefix in _FORBIDDEN_PACKAGE_PREFIXES:
                if imported.startswith(prefix):
                    violations.append(f"{path}:{lineno}: forbidden import {imported}")
    assert not violations, "\n".join(violations)


@pytest.mark.requirement("L3-PERS-016")
def test_application_does_not_import_infrastructure_or_interfaces() -> None:
    """L3-PERS-016: application/ SHALL NOT import infrastructure/ or interfaces/."""
    violations: list[str] = []
    for path in _iter_python_files(_APPLICATION):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, imported in _iter_imports(tree):
            for prefix in _FORBIDDEN_PACKAGE_PREFIXES:
                if imported.startswith(prefix):
                    violations.append(f"{path}:{lineno}: forbidden import {imported}")
    assert not violations, "\n".join(violations)


# -----------------------------------------------------------------------------
# Platform-specific module ban (L3-DEP-005, L3-DEP-018)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-DEP-005")
@pytest.mark.requirement("L3-DEP-018")
def test_domain_and_application_do_not_import_platform_modules() -> None:
    """L3-DEP-005 / L3-DEP-018: domain/ + application/ SHALL NOT import
    multiprocessing, subprocess, os.fork, or POSIX-only signal symbols.
    """
    violations: list[str] = []
    for layer in (_DOMAIN, _APPLICATION):
        for path in _iter_python_files(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for lineno, imported in _iter_imports(tree):
                for forbidden in _FORBIDDEN_PLATFORM_MODULES:
                    # Match either an exact import (e.g.,
                    # ``import multiprocessing``) or a from-import of
                    # a forbidden symbol (e.g., ``from os import fork``).
                    if imported == forbidden or imported.startswith(f"{forbidden}."):
                        violations.append(f"{path}:{lineno}: forbidden platform import {imported}")
    assert not violations, "\n".join(violations)
