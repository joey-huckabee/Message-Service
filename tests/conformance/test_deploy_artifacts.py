"""Conformance tests for the deployment artifacts.

These are inspection-style tests: they read the on-disk
deployment artifacts (`deploy/linux/message-service.service`,
`deploy/windows/README.md`, `pyproject.toml`, `poetry.lock`,
`.github/workflows/ci.yaml`) and assert that the directives,
commands, and metadata each spec statement requires are actually
present.

The tests are conformance-tier rather than unit-tier because
they verify that the **codebase itself** (not its runtime
behavior) obeys declared rules — the same shape as the
existing `test_pathlib_enforcement.py` and
`test_architecture_boundaries.py` tests.

Requirement references
----------------------
L1-DEP-001 (cross-platform portability)
L1-DEP-002 (systemd + NSSM), L1-DEP-003 (Poetry packaging)
L2-DEP-001, L2-DEP-004, L2-DEP-005, L2-DEP-007, L2-DEP-008, L2-DEP-009
L3-DEP-001, L3-DEP-002, L3-DEP-006, L3-DEP-007, L3-DEP-008,
L3-DEP-013, L3-DEP-014, L3-DEP-015
"""

from __future__ import annotations

import ast
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
# Proto dependency portability (L3-API-003)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-003")
def test_proto_dependency_is_tag_pinned_git_url(pyproject_data: dict[str, Any]) -> None:
    """L3-API-003: ``message-service-proto`` SHALL be a tag-pinned git URL.

    The local-path / develop-mode form bakes an absolute filesystem
    path (``file:///.../Message-Service-Proto``) into the built
    wheel's ``Requires-Dist`` metadata, making the artifact
    unusable on any machine where the sibling repo is not at the
    same path. Tag-pinning the git URL records the resolved SHA in
    ``poetry.lock`` so installs are deterministic AND portable.
    """
    deps = pyproject_data["tool"]["poetry"]["dependencies"]
    proto = deps.get("message-service-proto")
    assert proto is not None, "[tool.poetry.dependencies] missing the `message-service-proto` entry"
    assert isinstance(proto, dict), (
        f"`message-service-proto` should be an inline-table dependency, got: {proto!r}"
    )
    # Forbid the failure modes that break wheel portability.
    assert "path" not in proto, (
        f"L3-API-003 violation: `message-service-proto` uses a local path "
        f"({proto.get('path')!r}); the built wheel will carry an absolute "
        "file:// URL in Requires-Dist and fail to install on any other machine. "
        "Replace with `git = ..., tag = ...`."
    )
    assert not proto.get("develop"), (
        "L3-API-003 violation: `message-service-proto` declares `develop = true`; "
        "develop installs are local-only and not appropriate for the committed manifest."
    )
    # Require the portable form.
    assert "git" in proto, (
        f"L3-API-003 violation: `message-service-proto` lacks a `git` URL; got: {proto!r}"
    )
    assert proto["git"].startswith("https://"), (
        f"`message-service-proto.git` should be an https:// URL for CI portability; "
        f"got: {proto['git']!r}"
    )
    tag = proto.get("tag")
    assert tag, (
        f"L3-API-003 violation: `message-service-proto` is not tag-pinned; got: {proto!r}. "
        "Branch refs and bare commit SHAs lose human-readable provenance."
    )
    assert tag.startswith("v"), f"L3-API-003 expects a vX.Y.Z release tag; got: {tag!r}"


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


# -----------------------------------------------------------------------------
# Linux install demonstration artifact (L3-DEP-020)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def linux_install_demo_text() -> str:
    """Contents of the Linux install demonstration procedure document."""
    path = _REPO_ROOT / "docs" / "procedures" / "linux-install-demonstration.md"
    assert path.is_file(), f"L3-DEP-020 demonstration artifact missing at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DEP-020")
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
        "## Operator attestation",
    ],
)
def test_linux_install_demo_has_required_sections(
    linux_install_demo_text: str, section_marker: str
) -> None:
    """L3-DEP-020: the demonstration SHALL walk through unpack → running → shutdown."""
    assert section_marker in linux_install_demo_text, (
        f"L3-DEP-020 demonstration artifact missing required section: {section_marker!r}"
    )


# -----------------------------------------------------------------------------
# Dashboard visual demonstration artifact (L3-DASH-047)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dashboard_demo_text() -> str:
    """Contents of the dashboard demonstration procedure document."""
    path = _REPO_ROOT / "docs" / "procedures" / "dashboard-demonstration.md"
    assert path.is_file(), f"L3-DASH-047 demonstration artifact missing at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DASH-047")
@pytest.mark.parametrize(
    "section_marker",
    [
        "### Step 1 — Start the service",
        "### Step 2 — Login page",
        "### Step 3 — Administrator console",
        "### Step 4 — Subscriptions console",
        "### Step 5 — Past-runs view + report viewer + resend",
        "### Step 6 — Run-status board",
        "### Step 7 — Metrics dashboard",
        "## Operator attestation",
    ],
)
def test_dashboard_demo_has_required_sections(
    dashboard_demo_text: str, section_marker: str
) -> None:
    """L3-DASH-047: the demonstration SHALL walk each rendered dashboard page.

    Asserts each per-page checkpoint heading + the attestation block is present
    so an operator following the document produces a signed visual-verification
    artifact.
    """
    assert section_marker in dashboard_demo_text, (
        f"L3-DASH-047 demonstration artifact missing required section: {section_marker!r}"
    )


# -----------------------------------------------------------------------------
# CI workflow matrix (L3-DEP-001)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ci_workflow_text() -> str:
    """The contents of `.github/workflows/ci.yaml`."""
    path = _REPO_ROOT / ".github" / "workflows" / "ci.yaml"
    assert path.is_file(), f"L3-DEP-001 CI workflow missing at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-DEP-001")
@pytest.mark.parametrize("runner", ["ubuntu-latest", "windows-latest"])
def test_ci_workflow_matrix_includes_runner(ci_workflow_text: str, runner: str) -> None:
    """L3-DEP-001: the GitHub Actions matrix SHALL include both runners.

    Inspection-only: scans for the literal runner name appearing in
    the workflow YAML. The pytest job's matrix.os list is the
    authoritative source; this test fails if either runner is removed.
    """
    assert runner in ci_workflow_text, (
        f"CI workflow missing required matrix runner {runner!r}; "
        "L3-DEP-001 mandates ubuntu-latest + windows-latest as a minimum"
    )


@pytest.mark.requirement("L3-DEP-001")
def test_ci_workflow_matrix_runs_full_pytest_suite(ci_workflow_text: str) -> None:
    """L3-DEP-001: the matrix runners SHALL execute the full pytest suite.

    A `poetry run pytest` invocation with no `-k`/`-m` filter and no
    path argument is sufficient — the suite-wide config in
    `pyproject.toml` carries the coverage gate and the layer markers.
    """
    # Match the existing CI structure: the bare `poetry run pytest`
    # invocation lives on its own line in the pytest job.
    assert "poetry run pytest" in ci_workflow_text, (
        "CI workflow does not invoke `poetry run pytest` — full-suite "
        "execution is what L3-DEP-001 requires"
    )


# -----------------------------------------------------------------------------
# skipif convention (L3-DEP-002)
# -----------------------------------------------------------------------------


def _iter_test_files() -> list[Path]:
    """Every ``.py`` file under ``tests/`` (excluding ``__pycache__``)."""
    tests_root = _REPO_ROOT / "tests"
    return sorted(p for p in tests_root.rglob("*.py") if "__pycache__" not in p.parts)


def _skipif_calls(tree: ast.AST) -> list[tuple[int, ast.Call]]:
    """Yield ``(lineno, call)`` for every ``@pytest.mark.skipif(...)`` decorator.

    Matches both ``@pytest.mark.skipif(...)`` and the ``@skipif(...)``
    form (`from pytest import mark; mark.skipif(...)` is rare but
    permitted). The check is structural: a Call whose attribute chain
    ends in ``skipif``.
    """
    out: list[tuple[int, ast.Call]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            target = decorator.func
            # Walk attribute chain: gather the trailing identifier.
            while isinstance(target, ast.Attribute):
                if target.attr == "skipif":
                    out.append((decorator.lineno, decorator))
                    break
                target = target.value
            else:
                # Plain Name: e.g. `skipif(...)`.
                if isinstance(target, ast.Name) and target.id == "skipif":
                    out.append((decorator.lineno, decorator))
    return out


def _skipif_has_reason(call: ast.Call) -> bool:
    """Whether a ``skipif(...)`` call carries a non-empty ``reason=`` kwarg."""
    for kw in call.keywords:
        if kw.arg != "reason":
            continue
        # Accept any non-empty string literal. Other expression forms
        # (f-string, name reference) are accepted as "documented" too —
        # the spec wants a reason, not specifically a literal.
        if isinstance(kw.value, ast.Constant):
            return isinstance(kw.value.value, str) and bool(kw.value.value.strip())
        return True
    return False


@pytest.mark.requirement("L3-DEP-002")
def test_skipif_decorators_carry_documented_reason() -> None:
    """L3-DEP-002: every ``@pytest.mark.skipif(...)`` SHALL declare ``reason=``.

    Walks every test file, finds every skipif decorator, and asserts
    the call has a non-empty ``reason=`` keyword argument. Reports the
    file + line + decorator source for any violation so a fix is a
    one-line edit.
    """
    violations: list[str] = []
    for path in _iter_test_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover — defensive
            continue
        for lineno, call in _skipif_calls(tree):
            if not _skipif_has_reason(call):
                violations.append(f"{path}:{lineno}: skipif() missing non-empty reason= kwarg")
    assert not violations, "L3-DEP-002 violations — every skipif must document why:\n" + "\n".join(
        violations
    )


# -----------------------------------------------------------------------------
# CI workflow shape (L3-CICD-001, L3-CICD-002, L3-CICD-003, L3-CICD-005,
# L3-CICD-009, L3-CICD-016, L3-CICD-017)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-001")
def test_ci_workflow_lives_at_canonical_path() -> None:
    """L3-CICD-001: the CI workflow SHALL live at ``.github/workflows/ci.yaml``;
    no alternative workflow file SHALL replicate the gate set.
    """
    workflows_dir = _REPO_ROOT / ".github" / "workflows"
    canonical = workflows_dir / "ci.yaml"
    assert canonical.is_file(), f"L3-CICD-001 canonical workflow missing at {canonical}"
    # Any other *.yml / *.yaml files would be acceptable only if they
    # don't replicate the gates. The simplest enforcement: there is
    # exactly one workflow file. If a future increment adds, say, a
    # release-tag workflow, this assertion is what flags the need to
    # confirm it doesn't duplicate gates.
    workflow_files = sorted(p for p in workflows_dir.glob("*.y*ml"))
    assert workflow_files == [canonical], (
        f"unexpected workflow files alongside ci.yaml: "
        f"{[p.name for p in workflow_files if p != canonical]}; "
        "L3-CICD-001 forbids alternative workflow files that replicate the gate set"
    )


@pytest.mark.requirement("L3-CICD-002")
@pytest.mark.parametrize(
    "matrix_directive",
    [
        "fail-fast: false",
        "os: [ubuntu-latest, windows-latest]",
        "python-version: ['3.12', '3.13']",
    ],
)
def test_ci_workflow_matrix_shape(ci_workflow_text: str, matrix_directive: str) -> None:
    """L3-CICD-002: the matrix SHALL declare ``os``, ``python-version``,
    and ``fail-fast: false`` exactly per the spec's literal.

    Stricter than L3-DEP-001 (which only required the two runners) —
    this also pins the Python-version axis and the cell-independence
    flag.
    """
    assert matrix_directive in ci_workflow_text, (
        f"CI workflow missing required matrix directive {matrix_directive!r}; "
        "L3-CICD-002 mandates this exact literal"
    )


@pytest.mark.requirement("L3-CICD-003")
def test_ci_workflow_pytest_invocation_is_bare(ci_workflow_text: str) -> None:
    """L3-CICD-003: the pytest invocation SHALL be bare ``poetry run pytest``
    with no path args, ``-k`` filter, or ``-m`` skip.

    A non-bare invocation (e.g., ``poetry run pytest tests/unit``) would
    silently exclude tiers from CI, defeating the cross-platform parity
    guarantee.
    """
    found = False
    for line in ci_workflow_text.splitlines():
        stripped = line.strip()
        # Match the workflow's `run: poetry run pytest` shape with no
        # trailing arguments. A trailing comment is allowed.
        if stripped.startswith("run:"):
            run_value = stripped.removeprefix("run:").strip()
            # Strip a trailing comment if present.
            if "#" in run_value:
                run_value = run_value.split("#", 1)[0].strip()
            if run_value == "poetry run pytest":
                found = True
                break
    assert found, (
        "CI workflow does not invoke `poetry run pytest` as a bare command; "
        "L3-CICD-003 forbids path args / -k / -m on the matrix-cell pytest run"
    )


@pytest.mark.requirement("L3-CICD-005")
@pytest.mark.parametrize(
    "trigger_fragment",
    [
        "push:",
        "branches: [main]",
        "pull_request: {}",
        "schedule:",
        "cron: '0 6 * * *'",
    ],
)
def test_ci_workflow_triggers(ci_workflow_text: str, trigger_fragment: str) -> None:
    """L3-CICD-005: workflow ``on:`` SHALL include push (main), pull_request,
    and a 06:00 UTC daily schedule.
    """
    assert trigger_fragment in ci_workflow_text, (
        f"CI workflow missing trigger fragment {trigger_fragment!r}; "
        "L3-CICD-005 mandates push (branches:[main]) + pull_request:{} + "
        "schedule:cron:'0 6 * * *'"
    )


@pytest.mark.requirement("L3-CICD-009")
@pytest.mark.parametrize(
    "artifact_directive",
    [
        "actions/upload-artifact@v4",
        "name: coverage-${{ matrix.os }}-${{ matrix.python-version }}",
    ],
)
def test_ci_workflow_artifact_upload_shape(ci_workflow_text: str, artifact_directive: str) -> None:
    """L3-CICD-009: coverage artifacts SHALL be uploaded via
    ``actions/upload-artifact@v4`` with a per-cell distinguishable name.
    """
    assert artifact_directive in ci_workflow_text, (
        f"CI workflow missing artifact-upload directive {artifact_directive!r}; "
        "L3-CICD-009 mandates this exact action and name shape"
    )


@pytest.mark.requirement("L3-CICD-016")
def test_ci_workflow_provenance_log_present(ci_workflow_text: str) -> None:
    """L3-CICD-016: provenance log SHALL be emitted as a single line of the
    form ``provenance: sha=... os=... python=... trigger=... ts=...``.

    Verified by asserting the literal prefix and each required key=
    fragment appears in the workflow.
    """
    required_fragments = [
        "provenance: sha=",
        "os=",
        "python=",
        "trigger=",
        "ts=",
    ]
    for fragment in required_fragments:
        assert fragment in ci_workflow_text, (
            f"CI workflow's provenance log missing fragment {fragment!r}; "
            "L3-CICD-016 mandates the full sha/os/python/trigger/ts shape"
        )


@pytest.mark.requirement("L3-CICD-017")
def test_ci_workflow_artifact_retention_days_explicit(ci_workflow_text: str) -> None:
    """L3-CICD-017: every ``upload-artifact`` step SHALL set
    ``retention-days: 30`` explicitly.

    Counts ``upload-artifact`` invocations and asserts there's a
    ``retention-days: 30`` line for each — protects against a future
    edit that drops the explicit value and silently inherits the
    GitHub default (currently 90, but operator-overridable).
    """
    upload_count = ci_workflow_text.count("uses: actions/upload-artifact")
    retention_count = ci_workflow_text.count("retention-days: 30")
    assert upload_count > 0, "no upload-artifact steps found in CI workflow"
    assert retention_count >= upload_count, (
        f"L3-CICD-017 violation: {upload_count} upload-artifact steps but only "
        f"{retention_count} `retention-days: 30` lines; every upload SHALL set "
        "retention-days explicitly"
    )


# -----------------------------------------------------------------------------
# Pre-commit gate (L3-CICD-006, L3-CICD-007)
# -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def precommit_config_text() -> str:
    """The contents of ``.pre-commit-config.yaml``."""
    path = _REPO_ROOT / ".pre-commit-config.yaml"
    assert path.is_file(), f"pre-commit config missing at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.requirement("L3-CICD-006")
def test_ci_workflow_runs_precommit_with_show_diff(ci_workflow_text: str) -> None:
    """L3-CICD-006: CI SHALL invoke ``pre-commit run --all-files
    --show-diff-on-failure``.

    The ``--show-diff-on-failure`` flag prints the formatter's intended
    changes when ruff-format would have edited a file, making local
    fixes one-shot rather than guess-and-check for contributors.
    """
    expected = "pre-commit run --all-files --show-diff-on-failure"
    assert expected in ci_workflow_text, (
        f"CI workflow missing the {expected!r} invocation; "
        "L3-CICD-006 mandates --show-diff-on-failure"
    )


@pytest.mark.requirement("L3-CICD-007")
def test_precommit_hooks_pinned_to_tagged_releases(precommit_config_text: str) -> None:
    """L3-CICD-007: every ``- repo:`` entry SHALL pin ``rev:`` to a
    tagged release; floating refs (``main``, ``master``, ``HEAD``,
    ``develop``) are forbidden.

    Walks every ``rev:`` line, strips quoting, and rejects any value
    that looks like a branch ref. Tagged releases use either ``vX.Y.Z``
    semver or a 40-char SHA (the latter is acceptable as long as it's
    deterministic — branch names are the failure mode the rule guards
    against).
    """
    forbidden_refs = {"main", "master", "HEAD", "develop", "trunk"}
    violations: list[str] = []
    for lineno, line in enumerate(precommit_config_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("rev:"):
            continue
        # Extract the rev value, stripping comments and quotes.
        rev_value = stripped.removeprefix("rev:").strip()
        if "#" in rev_value:
            rev_value = rev_value.split("#", 1)[0].strip()
        rev_value = rev_value.strip("'\"")
        if rev_value in forbidden_refs:
            violations.append(
                f".pre-commit-config.yaml:{lineno}: rev: {rev_value!r} is a branch "
                "ref, not a tagged release"
            )
    assert not violations, (
        "L3-CICD-007 violations — pre-commit hooks must pin to tagged releases:\n"
        + "\n".join(violations)
    )


# -----------------------------------------------------------------------------
# pyproject.toml addopts (L3-CICD-013)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-013")
def test_pyproject_pytest_addopts_contains_basetemp(pyproject_data: dict[str, Any]) -> None:
    """L3-CICD-013: ``[tool.pytest.ini_options] addopts`` SHALL contain the
    literal ``--basetemp=.pytest_tmp`` (relative, workspace-rooted, never
    absolute).
    """
    addopts = pyproject_data["tool"]["pytest"]["ini_options"]["addopts"]
    # addopts is a list of strings.
    assert "--basetemp=.pytest_tmp" in addopts, (
        f"pyproject [tool.pytest.ini_options] addopts missing "
        f"'--basetemp=.pytest_tmp'; current addopts = {addopts!r}"
    )


# -----------------------------------------------------------------------------
# Coverage gate (L3-CICD-008) and lockfile reproducibility (L3-CICD-015)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-CICD-008")
def test_pyproject_pytest_addopts_pins_coverage_floor(pyproject_data: dict[str, Any]) -> None:
    """L3-CICD-008: the coverage gate SHALL be enforced by ``addopts``
    containing ``--cov-fail-under=<N>``.

    The current floor is whatever value is in ``addopts``; this test
    only enforces that *some* floor is present, not its specific value
    (the value moves with the documented coverage ratchet in ROADMAP).
    """
    addopts = pyproject_data["tool"]["pytest"]["ini_options"]["addopts"]
    matching = [a for a in addopts if isinstance(a, str) and a.startswith("--cov-fail-under=")]
    assert matching, (
        f"pyproject [tool.pytest.ini_options] addopts missing "
        f"'--cov-fail-under=<N>'; current addopts = {addopts!r}; "
        "L3-CICD-008 mandates the coverage gate be enforced via addopts"
    )
    # Sanity: the floor is a positive integer between 1 and 100.
    floor_str = matching[0].removeprefix("--cov-fail-under=")
    assert floor_str.isdigit() and 1 <= int(floor_str) <= 100, (
        f"--cov-fail-under value {floor_str!r} is not a valid percentage; "
        "expected an integer in [1, 100]"
    )


@pytest.mark.requirement("L3-CICD-015")
def test_ci_workflow_runs_poetry_check_lock(ci_workflow_text: str) -> None:
    """L3-CICD-015: the reproducibility job SHALL run ``poetry check --lock``
    (Poetry 2.x) — the workflow SHALL fail if the lockfile is out of sync
    with ``pyproject.toml``.
    """
    assert "poetry check --lock" in ci_workflow_text, (
        "CI workflow missing 'poetry check --lock' invocation; "
        "L3-CICD-015 mandates this lockfile-reproducibility check"
    )


@pytest.mark.requirement("L2-CICD-012")
def test_check_added_large_files_does_not_exempt_lockfile(
    precommit_config_text: str,
) -> None:
    """L2-CICD-012: the ``check-added-large-files`` hook SHALL NOT exempt
    the lockfile from its size budget.

    L2-CICD-012 has no L3 children — verified directly here. Walks the
    pre-commit config, finds the ``check-added-large-files`` entry, and
    asserts no ``args:`` block carries an exemption (``--enforce-all``
    is acceptable; per-file ``exclude:`` referencing ``poetry.lock`` is
    not). The default hook config is exemption-free, so this test
    fails only on a deliberate regression.
    """
    lines = precommit_config_text.splitlines()
    in_hook = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "- id: check-added-large-files":
            in_hook = True
            continue
        if in_hook:
            # The hook's body ends at the next list item or the next
            # hook entry (different `- id:` line) or a non-indented line.
            if stripped.startswith("- id:") or (line and not line.startswith(" ")):
                break
            # Look for an exclude/exempt that mentions the lockfile.
            if "poetry.lock" in stripped:
                pytest.fail(
                    f".pre-commit-config.yaml:{idx + 1}: check-added-large-files "
                    f"appears to exempt poetry.lock — L2-CICD-012 forbids this. "
                    f"Offending line: {stripped!r}"
                )
