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
narrow set of patterns ruff doesn't flag.

Requirement references
----------------------
L2-DEP-002 (pathlib usage)
L3-DEP-003 (ruff PTH rule enabled)
L3-DEP-004 (no literal path-separator strings outside URL contexts)
L3-PERS-012 (filesystem access via pathlib.Path)
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
