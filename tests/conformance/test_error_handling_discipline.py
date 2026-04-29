"""Inspection tests for the error-handling discipline pinned by L2-ERR-009/010.

Covers:
- L3-ERR-012: each inbound interface has a single translate_exceptions
  function/decorator chokepoint.
- L3-ERR-013: background-task error handling distinguishes transient
  vs permanent failures.
- L3-ERR-019: ruff BLE001 + S110 + S112 enabled in pyproject.
- L3-ERR-020: ruff is the canonical enforcement (no parallel grep gate).
- L3-ERR-021: no `except BaseException`/SystemExit/KeyboardInterrupt/
  GeneratorExit in production code.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _PROJECT_ROOT / "pyproject.toml"
_SRC_DIR = _PROJECT_ROOT / "src" / "message_service"


def _ruff_select() -> list[str]:
    pyproject = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    select = pyproject["tool"]["ruff"]["lint"].get("select", [])
    return list(select)


# -----------------------------------------------------------------------------
# L3-ERR-019: ruff BLE001 + S110 + S112 enabled
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-019")
def test_ruff_blind_except_rule_enabled() -> None:
    """L3-ERR-019: BLE001 (blind-except) SHALL be in ruff select."""
    select = _ruff_select()
    assert "BLE" in select or "BLE001" in select, (
        f"ruff lint.select missing BLE/BLE001; current = {select!r}"
    )


@pytest.mark.requirement("L3-ERR-019")
def test_ruff_try_except_pass_rule_enabled() -> None:
    """L3-ERR-019: S110 (try-except-pass) SHALL be in ruff select."""
    select = _ruff_select()
    assert "S110" in select, f"ruff lint.select missing S110; current = {select!r}"


@pytest.mark.requirement("L3-ERR-019")
def test_ruff_try_except_continue_rule_enabled() -> None:
    """L3-ERR-019: S112 (try-except-continue) SHALL be in ruff select."""
    select = _ruff_select()
    assert "S112" in select, f"ruff lint.select missing S112; current = {select!r}"


# -----------------------------------------------------------------------------
# L3-ERR-020: no parallel grep gate (ruff is canonical)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-020")
def test_ruff_is_canonical_enforcement_no_parallel_grep_gate() -> None:
    """L3-ERR-020: ruff BLE/S110/S112 are the canonical enforcement;
    no separate CI grep gate SHALL exist for blind-except patterns
    (ruff's line-resolution diagnostics are stronger than grep can be).
    """
    workflows_dir = _PROJECT_ROOT / ".github" / "workflows"
    if not workflows_dir.exists():
        return
    for workflow in workflows_dir.glob("*.y*ml"):
        text = workflow.read_text(encoding="utf-8")
        # No standalone grep step targeting blind-except patterns.
        forbidden_patterns = [
            "grep.*except.*BaseException",
            "grep.*except.*Exception",
        ]
        for pattern in forbidden_patterns:
            assert not re.search(pattern, text), (
                f"L3-ERR-020: workflow {workflow} contains a parallel grep "
                f"gate for blind-except patterns; ruff already covers this"
            )


# -----------------------------------------------------------------------------
# L3-ERR-021: no `except BaseException`/SystemExit/KeyboardInterrupt/GeneratorExit in src/
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-021")
def test_base_exception_catches_only_at_translator_chokepoint() -> None:
    """L3-ERR-021: ``except BaseException`` is permitted ONLY at the
    gRPC servicer's translator chokepoint. Domain and application code
    SHALL NOT contain BaseException-family catches that would silently
    swallow signals.
    """
    forbidden = (
        "except BaseException",
        "except SystemExit",
        "except KeyboardInterrupt",
        "except GeneratorExit",
    )
    # The single permitted call site for `except BaseException` is the
    # gRPC servicer, where each catch immediately routes through
    # `translate_to_grpc_status(context, exc)` — the boundary translator.
    allowed_modules: frozenset[str] = frozenset(
        {
            "src/message_service/interfaces/grpc/servicer.py",
        }
    )
    violations: list[str] = []
    for path in _SRC_DIR.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(_PROJECT_ROOT).as_posix()
        if relative in allowed_modules:
            continue
        text = path.read_text(encoding="utf-8")
        for line_num, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Skip docstring / comment occurrences — only actual `except`
            # clauses count. We approximate "actual" by checking the line
            # ends with ` as <name>:` or just `:`.
            if stripped.startswith("#") or '"""' in line:
                continue
            for pattern in forbidden:
                if stripped.startswith(pattern) and stripped.endswith(":"):
                    violations.append(f"{relative}:{line_num}: `{pattern}`")
    assert not violations, (
        "Forbidden BaseException-family except clauses outside the translator "
        "chokepoint (L3-ERR-021):\n" + "\n".join(violations)
    )


# -----------------------------------------------------------------------------
# L3-ERR-012: each inbound interface has a single translate_exceptions chokepoint
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-012")
def test_grpc_interface_has_single_translate_exceptions_chokepoint() -> None:
    """L3-ERR-012: the gRPC interface SHALL implement its translation
    layer as a single function/decorator (named ``translate_to_grpc_status``
    in v1, plus the helper functions ``_translate_known`` and
    ``_translate_unexpected``); handler code SHALL NOT contain bare
    ``except MessageServiceError`` blocks.
    """
    error_mapping = (_SRC_DIR / "interfaces" / "grpc" / "error_mapping.py").read_text(
        encoding="utf-8"
    )
    assert "def translate_to_grpc_status" in error_mapping or (
        "def _translate_known" in error_mapping and "def _translate_unexpected" in error_mapping
    ), "gRPC translation layer SHALL exist as a named function (L3-ERR-012)"

    # Servicer methods SHALL NOT carry their own bare except MessageServiceError.
    servicer = (_SRC_DIR / "interfaces" / "grpc" / "servicer.py").read_text(encoding="utf-8")
    assert "except MessageServiceError" not in servicer, (
        "L3-ERR-012: servicer SHALL NOT contain bare `except MessageServiceError` "
        "blocks; route through the translator chokepoint"
    )


# -----------------------------------------------------------------------------
# L3-ERR-013: background-task error handling distinguishes transient/permanent
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-013")
def test_aiosmtplib_mailer_classifies_transient_vs_permanent() -> None:
    """L3-ERR-013: the mailer (a background-task error site) SHALL
    distinguish transient failures (retry-eligible) from permanent
    failures (fail-fast). v1 implements this via ``_classify_smtp_error``
    in the mailer adapter, returning either ``"transient"`` or
    ``"permanent"`` per L3-MAIL-005/006/007.
    """
    text = (_SRC_DIR / "infrastructure" / "email" / "aiosmtplib_mailer.py").read_text(
        encoding="utf-8"
    )
    assert "_classify_smtp_error" in text, (
        "L3-ERR-013: aiosmtplib_mailer SHALL declare _classify_smtp_error "
        "(transient vs permanent classification)"
    )
    # The classifier SHALL return both branches.
    assert '"transient"' in text or "'transient'" in text
    assert '"permanent"' in text or "'permanent'" in text
