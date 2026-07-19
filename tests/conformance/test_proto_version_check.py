"""Conformance: the proto-version-mismatch check (L3-API-004).

The script's filename uses hyphens (CLI convention), so it is loaded via
importlib spec rather than a normal import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check-proto-version.py"


def _load() -> ModuleType:
    """Load the hyphen-named script as a module by file path."""
    spec = importlib.util.spec_from_file_location("check_proto_version", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_M = _load()


@pytest.mark.requirement("L3-API-004")
def test_script_exists() -> None:
    """The check script ships at its documented path."""
    assert _SCRIPT.is_file()


@pytest.mark.requirement("L3-API-004")
def test_normalize_tag_strips_leading_v() -> None:
    """A single leading 'v' is stripped; other strings pass through."""
    assert _M.normalize_tag("v0.1.1") == "0.1.1"
    assert _M.normalize_tag("0.1.1") == "0.1.1"


@pytest.mark.requirement("L3-API-004")
def test_pinned_proto_tag_parses_git_tag_dependency() -> None:
    """The tag is extracted from the git+tag dependency table."""
    text = (
        "[tool.poetry.dependencies]\n"
        'message-service-proto = { git = "https://x/y.git", tag = "v0.1.1" }\n'
    )
    assert _M.pinned_proto_tag(text) == "v0.1.1"


@pytest.mark.requirement("L3-API-004")
def test_pinned_proto_tag_none_when_absent() -> None:
    """A missing dependency or tag yields None."""
    assert _M.pinned_proto_tag("[tool.poetry.dependencies]\n") is None
    assert (
        _M.pinned_proto_tag('[tool.poetry.dependencies]\nmessage-service-proto = { git = "x" }\n')
        is None
    )


@pytest.mark.requirement("L3-API-004")
def test_evaluate_match_returns_exit_ok() -> None:
    """A pinned tag equal (v-stripped) to the installed version → exit 0."""
    code, _ = _M.evaluate("v1.2.3", "1.2.3")
    assert code == _M.EXIT_OK


@pytest.mark.requirement("L3-API-004")
def test_evaluate_mismatch_returns_exit_mismatch() -> None:
    """Disagreement → exit 1, naming both versions."""
    code, message = _M.evaluate("v1.2.3", "9.9.9")
    assert code == _M.EXIT_MISMATCH
    assert "1.2.3" in message and "9.9.9" in message


@pytest.mark.requirement("L3-API-004")
def test_evaluate_undeterminable_returns_exit_2() -> None:
    """A missing pinned tag or installed version → exit 2."""
    assert _M.evaluate(None, "1.2.3")[0] == _M.EXIT_UNDETERMINABLE
    assert _M.evaluate("v1.2.3", None)[0] == _M.EXIT_UNDETERMINABLE


@pytest.mark.requirement("L3-API-004")
def test_main_passes_against_the_repo() -> None:
    """End-to-end: the check exits 0 against the committed pyproject + installed proto."""
    assert _M.main() == _M.EXIT_OK
