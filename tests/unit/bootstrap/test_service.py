"""Unit tests for :mod:`message_service.bootstrap.service`.

These tests verify the composition root: given a valid :class:`Config`,
:func:`build_service` produces a :class:`Service` whose adapter
instances are of the expected concrete types and whose use cases are
wired with values pulled from the config. Full end-to-end behavior is
covered by ``tests/integration/test_full_pipeline.py``; the bootstrap
tests focus on the wiring itself.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from message_service.application.ports.template_repository import TemplateRepository
from message_service.bootstrap import Service, build_service, shutdown_service
from message_service.bootstrap.service import (
    _ensure_report_directory,
    _resolve_body_template_overrides,
)
from message_service.config.loader import load_config
from message_service.config.schema import TemplateRefConfig
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import ConfigurationError
from message_service.infrastructure.email.aiosmtplib_mailer import AiosmtplibMailer
from message_service.infrastructure.persistence.audit_archive_writer import (
    FilesystemAuditArchiveWriter,
)
from message_service.infrastructure.persistence.filesystem.report_store import (
    FilesystemReportStore,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from message_service.infrastructure.scheduler.asyncio_scheduler import (
    AsyncioBackgroundTaskScheduler,
)
from message_service.infrastructure.tags.vocabulary_loader import InMemoryTagVocabulary
from message_service.infrastructure.templating.manifest_loader import (
    InMemoryTemplateRepository,
)
from message_service.infrastructure.templating.renderer import (
    Jinja2SandboxedTemplateRenderer,
)
from message_service.infrastructure.time.system_clock import SystemClock

pytestmark = pytest.mark.allow_io


# -----------------------------------------------------------------------------
# Fixtures — build a minimal valid config on disk
# -----------------------------------------------------------------------------


def _write_config(tmp_path: Path) -> Path:
    """Construct the smallest valid config directory tree and return the TOML path."""
    # Templates
    (tmp_path / "body.html.j2").write_text("<p>{{ run_id }}</p>")
    (tmp_path / "frag.html.j2").write_text("<p>{{ v }}</p>")
    (tmp_path / "templates.toml").write_text(
        """
[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "body.html.j2"

[[template]]
name = "frag"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "frag.html.j2"
"""
    )
    # Tags
    (tmp_path / "tags.toml").write_text(
        """
[[tag]]
name = "production"

[[tag]]
name = "critical"
"""
    )
    # Top-level config
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f"""
[grpc]
host = "0.0.0.0"
port = 50051

[dashboard]
host = "0.0.0.0"
port = 8080

[persistence]
sqlite_path = "{(tmp_path / "svc.db").as_posix()}"

[persistence.filesystem]
report_directory = "{(tmp_path / "reports").as_posix()}"

[templates]
manifest_path = "{(tmp_path / "templates.toml").as_posix()}"
max_context_bytes = 524288
max_rendered_bytes = 5242880

[templates.email_body_template_ref]
name = "email_body"
version = "1.0"

[tags]
vocabulary_path = "{(tmp_path / "tags.toml").as_posix()}"

[pipelines]
registered = ["etl-nightly", "backup-daily"]

[mail]
from_address = "svc@example.com"
max_email_size_bytes = 10485760

[mail.smtp]
host = "smtp.example.com"
port = 587
username = "svc-user"
password = "secret"
use_starttls = true

[mail.retry]
max_retries = 3
initial_interval_seconds = 1
max_interval_seconds = 60
"""
    )
    return config_path


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[Service]:
    """Build a fully-composed Service from a minimal valid config.

    Teardown calls :func:`shutdown_service` unconditionally. Both
    :func:`shutdown_service` itself and its underlying primitives
    (``scheduler.begin_shutdown``, ``uow_factory.close``) are
    idempotent, so tests that end with an explicit
    ``await shutdown_service(service, ...)`` still work — the
    fixture-level second call is a safe no-op.
    """
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    svc = await build_service(config)
    try:
        yield svc
    finally:
        await shutdown_service(svc, timeout=1.0)


# -----------------------------------------------------------------------------
# Composition — correct concrete types for every port
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-CFG-001")
async def test_build_service_returns_service(service: Service) -> None:
    assert isinstance(service, Service)
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-037")
async def test_build_service_closes_connection_on_post_migration_failure(
    tmp_path: Path,
) -> None:
    """A failure after the connection opens SHALL close it (no leaked connection).

    Regression: only a migration failure closed the connection; a failure in
    any later step left it open, leaking an fd + aiosqlite's background thread.
    """
    from message_service.bootstrap import service as bootstrap_service
    from message_service.infrastructure.persistence.connection import open_connection

    config = load_config(_write_config(tmp_path))

    close_calls: list[bool] = []
    real_open = open_connection

    async def _spy_open(path: object) -> object:
        conn = await real_open(path)  # type: ignore[arg-type]
        orig_close = conn.close

        async def _tracked_close() -> None:
            close_calls.append(True)
            await orig_close()

        conn.close = _tracked_close  # type: ignore[method-assign]
        return conn

    # Force a failure at a post-migration step (tag-vocabulary load).
    with (
        patch.object(bootstrap_service, "open_connection", _spy_open),
        patch.object(
            bootstrap_service,
            "load_tag_vocabulary",
            side_effect=ConfigurationError("boom", details={}),
        ),
        pytest.raises(ConfigurationError, match="boom"),
    ):
        await build_service(config)

    # The connection opened for this build was closed exactly once.
    assert close_calls == [True]


@pytest.mark.asyncio
async def test_clock_is_system_clock(service: Service) -> None:
    assert isinstance(service.clock, SystemClock)
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_tag_vocabulary_loaded_from_toml(service: Service) -> None:
    assert isinstance(service.tag_vocabulary, InMemoryTagVocabulary)
    assert service.tag_vocabulary.contains("production")
    assert service.tag_vocabulary.contains("critical")
    assert not service.tag_vocabulary.contains("unknown")
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_template_repo_loaded_from_manifest(service: Service) -> None:
    from message_service.domain.aggregates.template_ref import TemplateRef

    assert isinstance(service.template_repo, InMemoryTemplateRepository)
    assert service.template_repo.exists(TemplateRef(name="email_body", version="1.0"))
    assert service.template_repo.exists(TemplateRef(name="frag", version="1.0"))
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_template_renderer_has_configured_size_limits(service: Service) -> None:
    assert isinstance(service.template_renderer, Jinja2SandboxedTemplateRenderer)
    # Verified indirectly via the renderer's private fields. These are
    # the config values we wrote above.
    assert service.template_renderer._max_context_bytes == 524_288
    assert service.template_renderer._max_rendered_bytes == 5_242_880
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_mailer_configured_from_mail_section(service: Service) -> None:
    assert isinstance(service.mailer, AiosmtplibMailer)
    assert service.mailer._host == "smtp.example.com"
    assert service.mailer._port == 587
    assert service.mailer._username == "svc-user"
    assert service.mailer._use_starttls is True
    assert service.mailer._max_retries == 3
    assert service.mailer._max_email_size_bytes == 10_485_760
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_scheduler_is_asyncio_background_scheduler(service: Service) -> None:
    assert isinstance(service.scheduler, AsyncioBackgroundTaskScheduler)
    # The sweeper loop is constructed but not started by build_service
    # (the CLI entrypoint does that explicitly after the gRPC server
    # is up), so no background tasks are active at this point.
    assert service.scheduler.active_task_count == 0
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_uow_factory_is_sqlite(service: Service) -> None:
    assert isinstance(service.uow_factory, SqliteUnitOfWorkFactory)
    await shutdown_service(service, timeout=1.0)


# -----------------------------------------------------------------------------
# Filesystem report store (Increment 19c)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_report_store_is_filesystem_adapter(service: Service) -> None:
    """build_service SHALL expose a :class:`FilesystemReportStore` instance."""
    assert isinstance(service.report_store, FilesystemReportStore)
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-010")
async def test_build_service_creates_missing_report_directory(
    service: Service, tmp_path: Path
) -> None:
    """L3-PERS-010: a missing report directory SHALL be created at startup."""
    # The fixture's _write_config points report_directory at
    # ``tmp_path / "reports"`` which did not exist before build.
    assert (tmp_path / "reports").is_dir()
    await shutdown_service(service, timeout=1.0)


@pytest.mark.requirement("L3-PERS-010")
def test_ensure_report_directory_creates_missing(tmp_path: Path) -> None:
    """L3-PERS-010: ``mkdir(parents=True, exist_ok=True)`` SHALL be applied."""
    target = tmp_path / "nested" / "reports"
    assert not target.exists()
    _ensure_report_directory(target)
    assert target.is_dir()


@pytest.mark.requirement("L3-PERS-010")
def test_ensure_report_directory_no_op_when_already_exists(tmp_path: Path) -> None:
    """An already-existing directory SHALL pass without error."""
    target = tmp_path / "reports"
    target.mkdir()
    _ensure_report_directory(target)
    assert target.is_dir()


@pytest.mark.requirement("L3-PERS-010")
def test_ensure_report_directory_raises_configuration_error_when_mkdir_fails(
    tmp_path: Path,
) -> None:
    """L3-PERS-010: ``mkdir`` failure SHALL surface as :class:`ConfigurationError`."""
    target = tmp_path / "reports"
    with (
        patch.object(Path, "mkdir", side_effect=OSError("permission denied")),
        pytest.raises(ConfigurationError) as excinfo,
    ):
        _ensure_report_directory(target)
    assert "report directory" in str(excinfo.value)
    assert excinfo.value.details.get("path") == str(target)


@pytest.mark.requirement("L3-PERS-011")
def test_ensure_report_directory_raises_configuration_error_when_unwritable(
    tmp_path: Path,
) -> None:
    """L3-PERS-011: unwritable existing directory SHALL surface as :class:`ConfigurationError`."""
    target = tmp_path / "reports"
    target.mkdir()
    # Patch only the probe write (not directory creation).
    with (
        patch.object(Path, "write_text", side_effect=OSError("read-only filesystem")),
        pytest.raises(ConfigurationError) as excinfo,
    ):
        _ensure_report_directory(target)
    assert "not writable" in str(excinfo.value)


@pytest.mark.requirement("L3-PERS-011")
def test_ensure_report_directory_removes_probe_file(tmp_path: Path) -> None:
    """The write-probe file SHALL NOT remain after a successful check."""
    target = tmp_path / "reports"
    target.mkdir()
    _ensure_report_directory(target)
    # Probe was named ``.write_probe`` and SHALL have been unlinked.
    assert not (target / ".write_probe").exists()


# -----------------------------------------------------------------------------
# Use cases — the four are all built and are usable through a UoW
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uow_factory_produces_working_uow(service: Service) -> None:
    """The UoW factory returned by the service SHALL yield a functional UoW
    that can transact against the migrated DB."""
    async with service.uow_factory() as uow:
        # The migration ran, so the tables exist. A read against an
        # empty audit_log should return an empty sequence, not raise.
        events = await uow.audit_log.query()
        assert list(events) == []
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_use_cases_all_populated(service: Service) -> None:
    """All four use cases SHALL be non-None after build."""
    assert service.begin_run is not None
    assert service.submit_stage_report is not None
    assert service.finalize_run is not None
    assert service.assemble_and_deliver is not None
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AUTH-001")
async def test_auth_components_populated_from_config(service: Service) -> None:
    """build_service SHALL wire the password hasher and the Login/Logout
    use cases from the ``[auth]`` section."""
    from message_service.application.use_cases.login import LoginUseCase
    from message_service.application.use_cases.logout import LogoutUseCase
    from message_service.infrastructure.auth.argon2_hasher import (
        Argon2PasswordHasher,
    )

    assert isinstance(service.password_hasher, Argon2PasswordHasher)
    assert isinstance(service.login, LoginUseCase)
    assert isinstance(service.logout, LogoutUseCase)
    # Login + Logout SHALL share the hasher singleton (L3-AUTH-001).
    assert service.login._hasher is service.password_hasher
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
async def test_pipeline_registry_reflects_config(service: Service) -> None:
    """BeginRun use case's pipeline registry SHALL equal the configured list."""
    # BeginRun stores the registry as a private frozenset; we can access
    # it for verification.
    assert service.begin_run._pipeline_registry == frozenset({"etl-nightly", "backup-daily"})
    await shutdown_service(service, timeout=1.0)


# -----------------------------------------------------------------------------
# Shutdown
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_closes_scheduler_and_connection(service: Service) -> None:
    """After shutdown, the scheduler SHALL reject new work and the connection SHALL be closed."""
    await shutdown_service(service, timeout=1.0)

    # Scheduler is in shutdown mode.
    async def _dummy() -> None:
        pass

    with pytest.raises(RuntimeError, match="shutting down"):
        service.scheduler.schedule(_dummy())

    # The UoW factory's connection is closed; opening a UoW now
    # raises.
    with pytest.raises(Exception):  # noqa: B017 — any aiosqlite close-related error
        async with service.uow_factory():
            pass


@pytest.mark.asyncio
async def test_shutdown_drains_inflight_background_tasks(service: Service) -> None:
    """shutdown_service SHALL await in-flight background tasks before closing the connection."""
    import asyncio

    completed = asyncio.Event()

    async def background_work() -> None:
        await asyncio.sleep(0.05)
        completed.set()

    service.scheduler.schedule(background_work())

    await shutdown_service(service, timeout=2.0)

    assert completed.is_set()


# -----------------------------------------------------------------------------
# Migrations applied at build
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migrations_applied_at_startup(service: Service) -> None:
    """build_service SHALL apply migrations so the schema is ready on first UoW."""
    async with service.uow_factory() as uow:
        # If the runs table didn't exist, this would raise sqlite3.OperationalError.
        async with uow._conn.execute("SELECT COUNT(*) FROM runs") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == 0
    await shutdown_service(service, timeout=1.0)


# -----------------------------------------------------------------------------
# Per-pipeline email-body template overrides — startup manifest validation
# (L3-TMPL-034)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-TMPL-034")
def test_resolve_body_template_overrides_empty_is_empty() -> None:
    """An empty override mapping resolves to an empty mapping (no manifest hits)."""
    repo = MagicMock(spec=TemplateRepository)
    assert _resolve_body_template_overrides({}, repo) == {}
    repo.exists.assert_not_called()


@pytest.mark.requirement("L3-TMPL-034")
def test_resolve_body_template_overrides_manifest_present_resolves() -> None:
    """A ref present in the manifest resolves to a TemplateRef."""
    repo = MagicMock(spec=TemplateRepository)
    repo.exists.return_value = True
    overrides = {"etl-nightly": TemplateRefConfig(name="nightly_body", version="2.0")}

    resolved = _resolve_body_template_overrides(overrides, repo)

    assert resolved == {"etl-nightly": TemplateRef(name="nightly_body", version="2.0")}
    repo.exists.assert_called_once_with(TemplateRef(name="nightly_body", version="2.0"))


@pytest.mark.requirement("L3-TMPL-034")
def test_resolve_body_template_overrides_absent_raises_configuration_error() -> None:
    """A ref absent from the manifest raises ConfigurationError with details."""
    repo = MagicMock(spec=TemplateRepository)
    repo.exists.return_value = False
    overrides = {"etl-nightly": TemplateRefConfig(name="missing", version="9.9")}

    with pytest.raises(ConfigurationError) as exc_info:
        _resolve_body_template_overrides(overrides, repo)

    assert exc_info.value.details["pipeline_type"] == "etl-nightly"
    assert exc_info.value.details["name"] == "missing"
    assert exc_info.value.details["version"] == "9.9"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-TMPL-034")
async def test_build_service_wires_valid_body_override(tmp_path: Path) -> None:
    """build_service resolves a manifest-present override into the use case.

    Proves the config → _resolve_body_template_overrides → use-case path end
    to end (the minimal manifest declares email_body@1.0 and registers
    etl-nightly).
    """
    config_path = _write_config(tmp_path)
    with config_path.open("a", encoding="utf-8") as fh:
        fh.write(
            "\n[pipelines.email_body_template_overrides]\n"
            'etl-nightly = { name = "email_body", version = "1.0" }\n'
        )
    config = load_config(config_path)
    svc = await build_service(config)
    try:
        assert svc.assemble_and_deliver._email_body_template_overrides == {
            "etl-nightly": TemplateRef(name="email_body", version="1.0")
        }
    finally:
        await shutdown_service(svc, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-041")
async def test_audit_pruner_has_no_archive_writer_by_default(service: Service) -> None:
    """With no archive_directory configured, the pruner archives nothing."""
    assert service.audit_log_pruner._archive_writer is None
    await shutdown_service(service, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-OBS-041")
async def test_build_service_wires_audit_archive_writer_and_creates_dir(tmp_path: Path) -> None:
    """A configured archive_directory is created and wired into the pruner."""
    config_path = _write_config(tmp_path)
    archive_dir = tmp_path / "audit-archive"
    with config_path.open("a", encoding="utf-8") as fh:
        fh.write(f'\n[observability.audit]\narchive_directory = "{archive_dir.as_posix()}"\n')
    config = load_config(config_path)
    svc = await build_service(config)
    try:
        assert isinstance(svc.audit_log_pruner._archive_writer, FilesystemAuditArchiveWriter)
        assert archive_dir.is_dir()  # created + probe-validated at startup
    finally:
        await shutdown_service(svc, timeout=1.0)
