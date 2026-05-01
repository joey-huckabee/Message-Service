"""Conformance tests for the runnable demonstration examples (Increment 28).

The ``examples/`` directory ships eight self-contained scenarios plus
a shared ``_lib/`` of helpers. These tests are inspection-only — they
read the on-disk artifacts and assert the structural invariants
declared by the Increment 28 spec. They deliberately do **not**
execute the scenarios in CI: each scenario boots a service
subprocess, an in-process SMTP capture, and waits up to 10s+ for
deliveries — total runtime would dominate the CI budget without
catching anything an inspection test couldn't catch first.

Smoke-quality assertions for the demos themselves are left to humans
running them locally. The pipeline-integration guide
(``docs/pipeline-integration-guide.md``) cross-references the
scenarios so a reader can correlate a documented RPC call with a
runnable example.

Spec reference
--------------
ROADMAP.md → Increment 28 → "Verification artifact" sub-step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# tests/conformance/test_examples_present.py → tests/conformance → tests → repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES_ROOT = _REPO_ROOT / "examples"
_LIB_DIR = _EXAMPLES_ROOT / "_lib"

# The scenarios listed in examples/README.md's table. Order matters
# because the README states a recommended walk order; this list
# captures the canonical set.
_SCENARIOS: tuple[str, ...] = (
    "01-hello-world",
    "02-multi-stage-aggregated",
    "03-per-stage-attachments",
    "04-retry-flow",
    "05-tag-routing",
    "06-orphan-detection",
    "07-manual-resend",
    "08-error-recovery",
)

# Required README headings per the Increment 28 spec.
_REQUIRED_README_SECTIONS: tuple[str, ...] = (
    "## What this demonstrates",
    "## Prerequisites",
    "## How to run",
    "## Expected output",
    "## What to look for",
    "## Cleanup",
    "## Troubleshooting",
)

# Shared helpers under examples/_lib/.
_REQUIRED_LIB_HELPERS: tuple[str, ...] = (
    "__init__.py",
    "smtp_capture.py",
    "service_runner.py",
    "pretty.py",
    "expectations.py",
    "common.py",
)


# -----------------------------------------------------------------------------
# Top-level layout
# -----------------------------------------------------------------------------


def test_examples_directory_exists() -> None:
    """The top-level ``examples/`` directory SHALL exist."""
    assert _EXAMPLES_ROOT.is_dir(), (
        f"Increment 28 deliverable missing: {_EXAMPLES_ROOT} is not a directory"
    )


def test_top_level_examples_readme_exists() -> None:
    """``examples/README.md`` SHALL exist as the index for the scenarios."""
    readme = _EXAMPLES_ROOT / "README.md"
    assert readme.is_file(), f"top-level examples index missing at {readme}"
    text = readme.read_text(encoding="utf-8")
    # Sanity: the file talks about the SMTP mock and lists scenarios.
    assert "aiosmtpd" in text or "SMTP" in text, (
        "examples/README.md should explain the in-process SMTP capture"
    )


# -----------------------------------------------------------------------------
# Shared helpers (_lib/)
# -----------------------------------------------------------------------------


def test_shared_lib_directory_exists() -> None:
    """``examples/_lib/`` SHALL exist."""
    assert _LIB_DIR.is_dir(), f"shared helpers directory missing at {_LIB_DIR}"


@pytest.mark.parametrize("filename", _REQUIRED_LIB_HELPERS)
def test_shared_lib_required_helper_present(filename: str) -> None:
    """Each required helper module under ``examples/_lib/`` SHALL exist."""
    helper = _LIB_DIR / filename
    assert helper.is_file(), f"required helper missing: {helper.relative_to(_REPO_ROOT)}"


# -----------------------------------------------------------------------------
# Scenario directories
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_scenario_directory_exists(scenario: str) -> None:
    """Each numbered scenario directory SHALL exist."""
    scenario_dir = _EXAMPLES_ROOT / scenario
    assert scenario_dir.is_dir(), (
        f"scenario directory missing: {scenario_dir.relative_to(_REPO_ROOT)}"
    )


@pytest.mark.parametrize("scenario", _SCENARIOS)
@pytest.mark.parametrize("filename", ["README.md", "run.py", "config.toml"])
def test_scenario_required_file_present(scenario: str, filename: str) -> None:
    """Every scenario SHALL contain ``README.md``, ``run.py``, and ``config.toml``."""
    path = _EXAMPLES_ROOT / scenario / filename
    assert path.is_file(), f"scenario file missing: {path.relative_to(_REPO_ROOT)}"


@pytest.mark.parametrize("scenario", _SCENARIOS)
@pytest.mark.parametrize("section", _REQUIRED_README_SECTIONS)
def test_scenario_readme_carries_required_section(scenario: str, section: str) -> None:
    """Every scenario README SHALL contain each required heading.

    The headings are spelled out by the Increment 28 spec
    (Prerequisites, What this demonstrates, How to run, Expected
    output, What to look for, Cleanup, Troubleshooting). Fixed
    heading names let readers predict the structure across all
    eight scenarios.
    """
    readme = _EXAMPLES_ROOT / scenario / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert section in text, (
        f"{readme.relative_to(_REPO_ROOT)} missing required section heading {section!r}"
    )


# -----------------------------------------------------------------------------
# Top-level README must list every scenario
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", _SCENARIOS)
def test_top_level_readme_lists_scenario(scenario: str) -> None:
    """The top-level ``examples/README.md`` table SHALL link each scenario.

    The README tracks the canonical walk order and acts as the entry
    point for new readers; missing a scenario from the index would
    leave it discoverable only by directory listing.
    """
    readme = _EXAMPLES_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert scenario in text, f"examples/README.md does not mention scenario directory {scenario!r}"


# -----------------------------------------------------------------------------
# .gitignore guards generated state
# -----------------------------------------------------------------------------


def test_gitignore_excludes_per_scenario_tmp_state() -> None:
    """``examples/.gitignore`` SHALL exclude the per-scenario ``.tmp/`` dir.

    Each scenario's ``run.py`` writes a SQLite database, WAL/SHM
    sidecars, and a rendered-report tree under ``.tmp/``. Without an
    exclusion, a developer who runs the demos and then ``git add``s
    the directory would accidentally commit gigabytes of
    runtime-generated artifacts.
    """
    gitignore = _EXAMPLES_ROOT / ".gitignore"
    assert gitignore.is_file(), (
        f"examples/.gitignore missing — runtime state ({_EXAMPLES_ROOT}/<scenario>/.tmp) "
        "would be staged on `git add examples/`"
    )
    text = gitignore.read_text(encoding="utf-8")
    assert ".tmp" in text, (
        "examples/.gitignore exists but does not mention `.tmp` — every "
        "scenario writes runtime state under .tmp/"
    )
