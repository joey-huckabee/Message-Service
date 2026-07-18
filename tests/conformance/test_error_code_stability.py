"""Conformance: the error-code stability lockfile + gate (L3-ERR-010 / L3-ERR-011).

Verifies the mechanic that freezes released error codes (L2-ERR-005 under
L1-ERR-002):

* the committed ``docs/error-codes.lock`` matches the current proto ``ErrorCode``
  enum,
* the comparison detects removals/renames (exit 1) and additions (exit 2),
* the lockfile round-trips through the parse/render helpers deterministically,
* both helper scripts exist and expose the documented exit-code contract.

The scripts are loaded via importlib spec because their filenames use hyphens
(CLI convention), making normal imports impossible. The module is registered in
``sys.modules`` before execution so its ``@dataclass`` can resolve its own
annotations.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_CHECK_SCRIPT = _SCRIPTS / "check-error-code-stability.py"
_UPDATE_SCRIPT = _SCRIPTS / "update-error-codes-lock.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load a hyphen-named script as a module by file path."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_CHECK = _load("check_error_code_stability", _CHECK_SCRIPT)


# -----------------------------------------------------------------------------
# L3-ERR-011 — helper scripts exist and expose the exit-code contract (Inspection)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-011")
def test_both_helper_scripts_exist() -> None:
    """Both the check and update scripts ship at their documented paths."""
    assert _CHECK_SCRIPT.is_file()
    assert _UPDATE_SCRIPT.is_file()


@pytest.mark.requirement("L3-ERR-011")
def test_exit_code_constants_match_contract() -> None:
    """The four exit codes are the distinct 0/1/2/3 pinned by L3-ERR-011."""
    codes = {
        _CHECK.EXIT_OK,
        _CHECK.EXIT_STABILITY_VIOLATION,
        _CHECK.EXIT_STALE_LOCKFILE,
        _CHECK.EXIT_LOCK_UNREADABLE,
    }
    assert codes == {0, 1, 2, 3}


@pytest.mark.requirement("L3-ERR-011")
def test_check_module_exposes_reusable_helpers() -> None:
    """The comparison helpers the update script + tests reuse are importable."""
    for name in ("current_error_codes", "parse_lockfile", "render_lockfile", "compare"):
        assert callable(getattr(_CHECK, name))


@pytest.mark.requirement("L3-ERR-011")
def test_update_script_regenerates_committed_lockfile_byte_for_byte() -> None:
    """The update script's rendering reproduces the committed lockfile exactly.

    Guards determinism: a Windows or Linux regenerate must not churn the file.
    """
    update = _load("update_error_codes_lock", _UPDATE_SCRIPT)
    check = update._load_check_module()
    rendered = check.render_lockfile(check.current_error_codes())
    committed = _CHECK.LOCK_PATH.read_text(encoding="utf-8")
    assert rendered == committed


# -----------------------------------------------------------------------------
# L3-ERR-010 — the lockfile + comparison gate (Analysis)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-ERR-010")
def test_committed_lockfile_matches_current_enum() -> None:
    """The committed lockfile is in sync with the current proto enum (exit 0)."""
    current = _CHECK.current_error_codes()
    locked = _CHECK.parse_lockfile(_CHECK.LOCK_PATH.read_text(encoding="utf-8"))
    result = _CHECK.compare(current, locked)
    assert result.added == []
    assert result.removed == []
    assert result.exit_code == _CHECK.EXIT_OK


@pytest.mark.requirement("L3-ERR-010")
def test_main_passes_against_committed_state() -> None:
    """End-to-end: the check exits 0 against the real committed lockfile."""
    assert _CHECK.main() == _CHECK.EXIT_OK


@pytest.mark.requirement("L3-ERR-010")
def test_removed_code_is_a_stability_violation() -> None:
    """A code in the lockfile but absent from the enum → exit 1."""
    current = {"ERROR_CODE_A", "ERROR_CODE_B"}
    locked = {"ERROR_CODE_A", "ERROR_CODE_B", "ERROR_CODE_C"}
    result = _CHECK.compare(current, locked)
    assert result.removed == ["ERROR_CODE_C"]
    assert result.added == []
    assert result.exit_code == _CHECK.EXIT_STABILITY_VIOLATION


@pytest.mark.requirement("L3-ERR-010")
def test_added_code_is_a_stale_lockfile() -> None:
    """A code in the enum but absent from the lockfile → exit 2."""
    current = {"ERROR_CODE_A", "ERROR_CODE_B", "ERROR_CODE_NEW"}
    locked = {"ERROR_CODE_A", "ERROR_CODE_B"}
    result = _CHECK.compare(current, locked)
    assert result.added == ["ERROR_CODE_NEW"]
    assert result.removed == []
    assert result.exit_code == _CHECK.EXIT_STALE_LOCKFILE


@pytest.mark.requirement("L3-ERR-010")
def test_rename_fails_as_violation_not_stale() -> None:
    """A rename surfaces as add+remove; the removal makes it exit 1, not 2."""
    current = {"ERROR_CODE_A", "ERROR_CODE_RENAMED"}
    locked = {"ERROR_CODE_A", "ERROR_CODE_OLD"}
    result = _CHECK.compare(current, locked)
    assert result.added == ["ERROR_CODE_RENAMED"]
    assert result.removed == ["ERROR_CODE_OLD"]
    assert result.exit_code == _CHECK.EXIT_STABILITY_VIOLATION


@pytest.mark.requirement("L3-ERR-010")
def test_main_reports_stale_lockfile_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns exit 2 when the lockfile is missing a current code."""
    current = _CHECK.current_error_codes()
    partial = _CHECK.render_lockfile(current - {min(current)})
    lock = tmp_path / "error-codes.lock"
    lock.write_text(partial, encoding="utf-8", newline="\n")
    monkeypatch.setattr(_CHECK, "LOCK_PATH", lock)
    assert _CHECK.main() == _CHECK.EXIT_STALE_LOCKFILE


@pytest.mark.requirement("L3-ERR-010")
def test_main_reports_missing_lockfile_exit_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() returns exit 3 when the lockfile does not exist."""
    monkeypatch.setattr(_CHECK, "LOCK_PATH", tmp_path / "does-not-exist.lock")
    assert _CHECK.main() == _CHECK.EXIT_LOCK_UNREADABLE


@pytest.mark.requirement("L3-ERR-010")
def test_parse_ignores_comments_and_blank_lines() -> None:
    """Comment and blank lines are ignored; only codes are parsed."""
    text = "# header\n\nERROR_CODE_A\n   \nERROR_CODE_B\n# trailer\n"
    assert _CHECK.parse_lockfile(text) == {"ERROR_CODE_A", "ERROR_CODE_B"}


@pytest.mark.requirement("L3-ERR-010")
def test_render_round_trips_and_is_sorted() -> None:
    """render → parse restores the set, and the body is sorted ascending."""
    codes = {"ERROR_CODE_C", "ERROR_CODE_A", "ERROR_CODE_B"}
    rendered = _CHECK.render_lockfile(codes)
    assert _CHECK.parse_lockfile(rendered) == codes
    body = [ln for ln in rendered.splitlines() if ln and not ln.startswith("#")]
    assert body == sorted(body)
