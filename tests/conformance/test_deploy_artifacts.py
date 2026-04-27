"""Conformance tests for the deployment artifacts.

These are inspection-style tests: they read the on-disk
deployment artifacts (`deploy/linux/message-service.service`,
`deploy/windows/README.md`, `pyproject.toml`, `poetry.lock`)
and assert that the directives, commands, and metadata each
spec statement requires are actually present.

The tests are conformance-tier rather than unit-tier because
they verify that the **codebase itself** (not its runtime
behavior) obeys declared rules — the same shape as the
existing `test_pathlib_enforcement.py` and
`test_architecture_boundaries.py` tests.

Requirement references
----------------------
L1-DEP-002 (systemd + NSSM), L1-DEP-003 (Poetry packaging)
L2-DEP-004, L2-DEP-005, L2-DEP-007, L2-DEP-008, L2-DEP-009
L3-DEP-006, L3-DEP-007, L3-DEP-008, L3-DEP-013, L3-DEP-014,
L3-DEP-015
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

# The repo root is two parents up from this file:
# tests/conformance/test_deploy_artifacts.py → tests/conformance → tests → repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


# -----------------------------------------------------------------------------
# Systemd unit (L3-DEP-006, L3-DEP-007)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def systemd_unit_text() -> str:
    """The contents of `deploy/linux/message-service.service`."""
    path = _REPO_ROOT / "deploy" / "linux" / "message-service.service"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DEP-006")
@pytest.mark.parametrize(
    "directive",
    [
        "Type=exec",
        "Restart=on-failure",
        "RestartSec=5s",
        "TimeoutStopSec=30s",
        "KillSignal=SIGTERM",
    ],
)
def test_systemd_unit_includes_runtime_directive(systemd_unit_text: str, directive: str) -> None:
    """L3-DEP-006: systemd unit SHALL include the named runtime directive."""
    assert directive in systemd_unit_text, f"systemd unit missing required directive {directive!r}"


@pytest.mark.requirement("L3-DEP-007")
@pytest.mark.parametrize(
    "directive",
    [
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateTmp=true",
        "ReadWritePaths=",
    ],
)
def test_systemd_unit_includes_sandboxing_directive(systemd_unit_text: str, directive: str) -> None:
    """L3-DEP-007: systemd unit SHALL include each sandboxing directive."""
    assert directive in systemd_unit_text, (
        f"systemd unit missing required sandboxing directive {directive!r}"
    )


@pytest.mark.requirement("L3-DEP-006")
def test_systemd_unit_environmentfile_passthrough(systemd_unit_text: str) -> None:
    """The unit SHALL include an optional ``EnvironmentFile=-`` directive
    so operators can drop credentials and per-host overrides into a
    sibling env-file without editing the unit. The leading hyphen
    makes the file optional; systemd will not fail-to-start if the
    file is absent.
    """
    assert "EnvironmentFile=-" in systemd_unit_text, (
        "systemd unit missing optional EnvironmentFile=- directive for "
        "credential/override passthrough"
    )


@pytest.mark.requirement("L3-DEP-007")
def test_systemd_unit_readwritepaths_not_empty(systemd_unit_text: str) -> None:
    """L3-DEP-007: ReadWritePaths SHALL be set (not just declared)."""
    # Find the `ReadWritePaths=` line and assert it has at least one path.
    for line in systemd_unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ReadWritePaths="):
            value = stripped.split("=", 1)[1].strip()
            assert value, "ReadWritePaths= is empty; SHALL declare at least one path"
            return
    pytest.fail("ReadWritePaths= directive not found")


# -----------------------------------------------------------------------------
# NSSM README (L3-DEP-008)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def nssm_readme_text() -> str:
    """The contents of `deploy/windows/README.md`."""
    path = _REPO_ROOT / "deploy" / "windows" / "README.md"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DEP-008")
@pytest.mark.parametrize(
    "command_fragment",
    [
        "nssm.exe install MessageService",
        "set MessageService DisplayName",
        "set MessageService Description",
        "set MessageService AppStdout",
        "set MessageService AppStderr",
        "AppStopMethodConsole 30000",
        "set MessageService ObjectName",
    ],
)
def test_nssm_readme_documents_required_command(
    nssm_readme_text: str, command_fragment: str
) -> None:
    """L3-DEP-008: NSSM README SHALL document each required nssm command."""
    assert command_fragment in nssm_readme_text, (
        f"NSSM README missing required command fragment: {command_fragment!r}"
    )


# -----------------------------------------------------------------------------
# pyproject.toml + poetry.lock (L3-DEP-013, L3-DEP-014, L3-DEP-015)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def pyproject_data() -> dict[str, Any]:
    """Parsed `pyproject.toml`."""
    path = _REPO_ROOT / "pyproject.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.requirement("L3-DEP-013")
def test_pyproject_python_version_constraint(pyproject_data: dict[str, Any]) -> None:
    """L3-DEP-013: `python = ">=3.12,<4.0"` in [tool.poetry.dependencies]."""
    deps = pyproject_data["tool"]["poetry"]["dependencies"]
    assert deps["python"] == ">=3.12,<4.0", (
        f"unexpected python constraint: {deps['python']!r}; expected '>=3.12,<4.0'"
    )


@pytest.mark.requirement("L3-DEP-014")
def test_poetry_lock_is_committed() -> None:
    """L3-DEP-014: `poetry.lock` SHALL be committed to the repository."""
    lockfile = _REPO_ROOT / "poetry.lock"
    assert lockfile.is_file(), f"poetry.lock missing at {lockfile}"
    # Sanity: the file has actual lock content (not empty).
    content = lockfile.read_text(encoding="utf-8")
    assert "[[package]]" in content, "poetry.lock has no [[package]] entries"


@pytest.mark.requirement("L3-DEP-015")
def test_pyproject_console_script_entry(pyproject_data: dict[str, Any]) -> None:
    """L3-DEP-015: `message-service = "message_service.interfaces.cli.main:main"`."""
    scripts = pyproject_data["tool"]["poetry"]["scripts"]
    assert "message-service" in scripts, "[tool.poetry.scripts] missing the `message-service` entry"
    assert scripts["message-service"] == "message_service.interfaces.cli.main:main", (
        f"unexpected console-script target: {scripts['message-service']!r}"
    )


# -----------------------------------------------------------------------------
# Windows install demonstration artifact (L3-DEP-009)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def windows_install_demo_text() -> str:
    """Contents of the Windows install demonstration procedure document."""
    path = _REPO_ROOT / "docs" / "procedures" / "windows-install-demonstration.md"
    assert path.is_file(), f"L3-DEP-009 demonstration artifact missing at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DEP-009")
@pytest.mark.parametrize(
    "section_marker",
    [
        "### Step 1 — Unpack distribution",
        "### Step 2 — Install dependencies",
        "### Step 3 — Provision configuration",
        "### Step 4 — Create service account",
        "### Step 5 — Register the service",
        "### Step 6 — Start the service",
        "### Step 7 — Verify graceful shutdown",
        "### Step 8 — Verify restart cleans up",
        "## Attestation",
    ],
)
def test_windows_install_demo_has_required_sections(
    windows_install_demo_text: str, section_marker: str
) -> None:
    """L3-DEP-009: the demonstration SHALL walk through unpack → running service.

    Asserts each required step heading + the attestation form is
    present so an operator following the document can produce a
    signed verification artifact.
    """
    assert section_marker in windows_install_demo_text, (
        f"L3-DEP-009 demonstration artifact missing required section: {section_marker!r}"
    )
