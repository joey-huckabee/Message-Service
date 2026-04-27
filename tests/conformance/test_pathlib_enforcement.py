"""Pathlib-enforcement conformance test.

`L3-DEP-003` requires the ruff ``PTH`` ruleset to be enabled and
to fail the build on ``os.path.join`` or string ``/``
concatenation of paths in ``src/``. The ruff invocation in
pre-commit + CI is the primary enforcement; this test is the
belt-and-braces check that the rule has not been silently
disabled in ``pyproject.toml``.

Per `L3-DEP-004`, no string-typed filesystem constants in source
SHALL contain literal `\\` or `/` separators outside URL contexts
(URLs are inherently `/`-separated). The ruff ``PTH`` ruleset
catches most of this; this test additionally scans for the
narrow set of patterns ruff doesn't flag — module-level and
class-level ``str``-typed constants whose value contains a path
separator.

Requirement references
----------------------
L2-DEP-002 (pathlib usage)
L3-DEP-003 (ruff PTH rule enabled)
L3-DEP-004 (no literal path-separator strings outside URL contexts)
L3-PERS-012 (filesystem access via pathlib.Path)
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "message_service"


@pytest.fixture(scope="module")
def ruff_lint_config() -> dict[str, Any]:
    """Parsed ``[tool.ruff.lint]`` section of pyproject.toml."""
    pyproject: dict[str, Any] = tomllib.loads(
        (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    lint: dict[str, Any] = pyproject["tool"]["ruff"]["lint"]
    return lint


@pytest.mark.requirement("L3-DEP-003")
def test_ruff_pth_ruleset_enabled(ruff_lint_config: dict[str, Any]) -> None:
    """L3-DEP-003: the ruff ``PTH`` ruleset SHALL be in ``[tool.ruff.lint] select``.

    The ruff invocation in pre-commit + CI fails the build on any
    PTH violation; this test ensures the rule has not been
    silently disabled by a future config edit.
    """
    select = ruff_lint_config.get("select", [])
    assert "PTH" in select, f"ruff [tool.ruff.lint] select missing 'PTH'; current = {select!r}"


@pytest.mark.requirement("L3-DEP-003")
def test_ruff_pth_not_globally_ignored(ruff_lint_config: dict[str, Any]) -> None:
    """L3-DEP-003: ``PTH`` rules SHALL NOT be in ``[tool.ruff.lint] ignore``.

    Per-file ``# noqa: PTH...`` directives are still valid (they
    require an inline reason comment per the project's lint
    convention); blanket ignores in pyproject would make the rule
    a no-op, which the spec forbids.
    """
    ignore = ruff_lint_config.get("ignore", [])
    pth_ignored = [code for code in ignore if str(code).startswith("PTH")]
    assert not pth_ignored, (
        f"ruff [tool.ruff.lint] ignore globally suppresses PTH rules: {pth_ignored}"
    )


# -----------------------------------------------------------------------------
# Path-separator scan in str-typed constants (L3-DEP-004)
# -----------------------------------------------------------------------------


def _iter_src_python_files() -> list[Path]:
    """Every ``.py`` file under ``src/message_service/`` (excluding caches)."""
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _is_url_like(value: str) -> bool:
    """Whether the string is plausibly a URL (URI-scheme present).

    The spec carves URLs out of L3-DEP-004 because they are
    inherently ``/``-separated. We treat any string containing ``://``
    as URL-like, plus the ``urn:`` and ``mailto:`` prefixes which
    don't fit the ``://`` shape.
    """
    return "://" in value or value.startswith(("urn:", "mailto:"))


def _looks_like_path_separator_constant(value: str) -> bool:
    """Whether a str literal looks like a filesystem path constant.

    Returns True when the string contains a ``/`` or ``\\`` and isn't
    URL-like. We do **not** try to be smart about regex / format
    strings / SQL — module-level + class-level constants that contain
    literal path separators are exactly what L3-DEP-004 forbids,
    irrespective of intent. If a future use case legitimately needs
    such a constant, the spec change goes in `docs/L3-REQ.md` first.
    """
    if "/" not in value and "\\" not in value:
        return False
    return not _is_url_like(value)


def _walk_constant_assignments(
    body: list[ast.stmt],
) -> list[tuple[int, str, str]]:
    """Yield ``(lineno, name, value)`` for every str-typed constant assignment.

    Walks module-level + class-level ``ast.Assign`` and ``ast.AnnAssign``
    nodes. Function-body assignments are deliberately excluded —
    L3-DEP-004's intent is to ban *constants*, not local variables.
    """
    out: list[tuple[int, str, str]] = []
    for node in body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    out.append((node.lineno, target.id, value.value))
        elif isinstance(node, ast.ClassDef):
            out.extend(_walk_constant_assignments(node.body))
    return out


@pytest.mark.requirement("L3-DEP-004")
def test_no_literal_path_separators_in_str_constants() -> None:
    """L3-DEP-004: no module-/class-level str constant under ``src/`` SHALL
    contain a literal ``/`` or ``\\`` path separator (URL contexts excepted).

    The intent is that filesystem paths flow through ``pathlib.Path``
    construction; a literal path-separator string is the failure mode
    ruff PTH rules don't catch. Each violation reports file + line +
    constant name + value so a fix is a one-line edit (typically:
    rewrite as a ``Path(...)`` construction or a tuple of segments).
    """
    violations: list[str] = []
    for path in _iter_src_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover — defensive
            continue
        for lineno, name, value in _walk_constant_assignments(tree.body):
            if _looks_like_path_separator_constant(value):
                violations.append(
                    f"{path}:{lineno}: constant {name!r} = {value!r} "
                    f"contains a literal path separator"
                )
    assert not violations, (
        "L3-DEP-004 violations — replace with pathlib.Path(...) construction:\n"
        + "\n".join(violations)
    )
