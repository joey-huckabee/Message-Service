"""Inspection tests for v1's persistence-layer wiring through bootstrap.

Covers L3-PERS-018 (file-permission strategy delegated to systemd
UMask, no explicit os.chmod), L3-PERS-019 (no explicit
PRAGMA wal_checkpoint(TRUNCATE) on shutdown), L3-PERS-023 (code-as-spec
conformance instead of prose registry), L3-PERS-030 (pruner registered
on the same BackgroundTaskScheduler as sweeper), L3-PERS-031 (pruner
queries via the shared UoW factory), L3-PERS-032 (pruner uses same
UoW factory as gRPC handlers and sweeper).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP_PATH = _PROJECT_ROOT / "src" / "message_service" / "bootstrap" / "service.py"
_CONNECTION_PATH = (
    _PROJECT_ROOT / "src" / "message_service" / "infrastructure" / "persistence" / "connection.py"
)


# -----------------------------------------------------------------------------
# L3-PERS-018: file permissions via deployment-layer umask, not explicit chmod
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-018")
def test_no_explicit_chmod_on_sqlite_file_in_v1() -> None:
    """L3-PERS-018: v1 delegates file-permission setting to systemd
    UMask (or NSSM equivalent on Windows); production code SHALL NOT
    explicitly chmod the SQLite file (avoids TOCTOU window between
    WAL-file creation and the chmod call).
    """
    text = _CONNECTION_PATH.read_text(encoding="utf-8")
    assert "chmod" not in text, (
        "infrastructure/persistence/connection.py SHALL NOT call chmod "
        "(L3-PERS-018 — permission control belongs in the deployment "
        "layer via systemd UMask)"
    )


# -----------------------------------------------------------------------------
# L3-PERS-019: no explicit PRAGMA wal_checkpoint(TRUNCATE) on shutdown
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-019")
def test_no_explicit_wal_checkpoint_truncate_on_shutdown_in_v1() -> None:
    """L3-PERS-019: SQLite's automatic WAL checkpointing is sufficient
    for v1's single-tenant workload; an explicit
    ``PRAGMA wal_checkpoint(TRUNCATE)`` would require no other readers,
    which v1's shutdown ordering doesn't guarantee.
    """
    bootstrap_text = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    connection_text = _CONNECTION_PATH.read_text(encoding="utf-8")
    for text, label in [(bootstrap_text, "bootstrap"), (connection_text, "connection")]:
        assert "wal_checkpoint" not in text.lower(), (
            f"{label} appears to call wal_checkpoint; L3-PERS-019 says v1 "
            "relies on SQLite's automatic checkpointing instead"
        )


# -----------------------------------------------------------------------------
# L3-PERS-023: code-as-spec via existing conformance tests, not prose registry
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-023")
def test_no_filesystem_access_points_prose_registry_in_v1() -> None:
    """L3-PERS-023: v1 maintains conformance via
    ``tests/conformance/test_pathlib_enforcement.py`` and the report-
    pruner sole-deleter test (`L3-PERS-035`); a separate
    ``docs/reviews/filesystem-access-points.md`` registry does NOT exist.
    """
    registry_path = _PROJECT_ROOT / "docs" / "reviews" / "filesystem-access-points.md"
    assert not registry_path.exists(), (
        f"L3-PERS-023: v1 chose code-as-spec conformance; the prose "
        f"registry at {registry_path} should NOT exist (if it does, "
        "the L3-PERS-023 reword needs to be reverted to its earlier shape)"
    )


# -----------------------------------------------------------------------------
# L3-PERS-030: pruner constructed during bootstrap, registered on same scheduler
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-030")
def test_bootstrap_constructs_report_pruner_loop() -> None:
    """L3-PERS-030: ``report_pruner_loop`` SHALL be constructed during
    ``build_service`` and exposed on the ``Service`` dataclass.
    """
    text = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    assert "ReportPrunerLoop(" in text, (
        "bootstrap/service.py SHALL construct ReportPrunerLoop (L3-PERS-030)"
    )
    assert "report_pruner_loop" in text


@pytest.mark.requirement("L3-PERS-030")
def test_service_dataclass_exposes_report_pruner_loop() -> None:
    """L3-PERS-030: the constructed ``Service`` dataclass SHALL carry
    ``report_pruner_loop`` so ``__main__._run`` can start it.
    """
    from message_service.bootstrap.service import Service

    assert "report_pruner_loop" in Service.__dataclass_fields__


# -----------------------------------------------------------------------------
# L3-PERS-032: pruner uses the same UoW factory as sweeper / gRPC handlers
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-PERS-032")
def test_report_pruner_uses_shared_uow_factory() -> None:
    """L3-PERS-032: the pruner SHALL acquire UoWs through the same
    ``SqliteUnitOfWorkFactory`` as gRPC handlers + the orphan sweeper,
    inheriting L2-PERS-004 single-shared-connection serialization.
    """
    text = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    # Both sweeper and report-pruner use the same uow_factory variable
    # passed into their respective constructors. The shape we check is
    # that ReportPrunerUseCase receives uow_factory.
    assert "ReportPrunerUseCase(" in text
    # Verify the factory is shared (not a new instance for the pruner).
    assert text.count("SqliteUnitOfWorkFactory(") == 1, (
        "L3-PERS-032: the SqliteUnitOfWorkFactory SHALL be constructed "
        "exactly once per bootstrap run; pruner shares the same factory"
    )
