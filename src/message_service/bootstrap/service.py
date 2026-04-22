"""Service composition: config → adapters → use cases.

The bootstrap's job is purely wiring. It does not define any business
logic; every decision lives either in the config schema, the
infrastructure adapters, or the application use cases. Bootstrap just
says who gets which instance of what.

Construction order (matters — later steps depend on earlier ones):

1. Configure logging. Errors in construction need structured output.
2. Open the SQLite connection and apply migrations.
3. Build the Clock (needed by repos and use cases).
4. Build stateless adapters that don't depend on the DB — tag
   vocabulary, template repo, template renderer.
5. Build the Mailer (depends only on config).
6. Build the BackgroundTaskScheduler.
7. Build the UoW factory (depends on connection + clock + repo
   factories).
8. Build the four use cases with their ports injected.

Teardown reverses the critical parts: stop the scheduler (so
in-flight work can drain against a live connection), then close the
connection.

Requirement references
----------------------
L1-CFG-001 (TOML-driven composition)
L1-DEP-001 (single-process service)
L2-RUN-013 (background task coordination)
L2-PERS-002 (DB setup at startup)
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite
import structlog

from message_service.application.use_cases.assemble_and_deliver import (
    AssembleAndDeliverUseCase,
)
from message_service.application.use_cases.begin_run import BeginRunUseCase
from message_service.application.use_cases.finalize_run import FinalizeRunUseCase
from message_service.application.use_cases.submit_stage_report import (
    SubmitStageReportUseCase,
)
from message_service.config.schema import Config
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.infrastructure.email.aiosmtplib_mailer import AiosmtplibMailer
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from message_service.infrastructure.scheduler.asyncio_scheduler import (
    AsyncioBackgroundTaskScheduler,
)
from message_service.infrastructure.tags.vocabulary_loader import (
    InMemoryTagVocabulary,
    load_tag_vocabulary,
)
from message_service.infrastructure.templating.manifest_loader import (
    InMemoryTemplateRepository,
    load_template_manifest,
)
from message_service.infrastructure.templating.renderer import (
    Jinja2SandboxedTemplateRenderer,
)
from message_service.infrastructure.time.system_clock import SystemClock
from message_service.observability.logging_setup import configure_logging

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Service:
    """Composed service state.

    A frozen dataclass of every adapter instance and every use case. The
    gRPC servicer and the HTTP dashboard both receive a reference to
    ``Service`` and reach into it for the pieces they need. No
    global state, no service locator, no singletons.

    Adapter instances are exposed on the service (not just the use
    cases) because some interfaces layer code — for instance, the
    dashboard's "list runs" endpoint — needs direct access to a repo
    without going through a use case.

    Attributes:
        config: The parsed, frozen configuration.
        clock: :class:`SystemClock` (injected into use cases and some
            adapters).
        tag_vocabulary: In-memory tag vocabulary.
        template_repo: In-memory template repository.
        template_renderer: Sandboxed Jinja2 renderer.
        mailer: SMTP mailer.
        scheduler: Asyncio background-task scheduler.
        uow_factory: UoW factory closed over the shared SQLite
            connection + per-UoW repo factories.
        begin_run: :class:`BeginRunUseCase`.
        submit_stage_report: :class:`SubmitStageReportUseCase`.
        finalize_run: :class:`FinalizeRunUseCase`.
        assemble_and_deliver: :class:`AssembleAndDeliverUseCase`
            (not directly invoked by request handlers; exposed for
            testing and for the FinalizeRun background-task factory
            that :class:`FinalizeRunUseCase` closes over).
    """

    config: Config
    clock: SystemClock
    tag_vocabulary: InMemoryTagVocabulary
    template_repo: InMemoryTemplateRepository
    template_renderer: Jinja2SandboxedTemplateRenderer
    mailer: AiosmtplibMailer
    scheduler: AsyncioBackgroundTaskScheduler
    uow_factory: SqliteUnitOfWorkFactory
    begin_run: BeginRunUseCase
    submit_stage_report: SubmitStageReportUseCase
    finalize_run: FinalizeRunUseCase
    assemble_and_deliver: AssembleAndDeliverUseCase


async def build_service(config: Config) -> Service:
    """Construct a fully-wired :class:`Service` from a validated :class:`Config`.

    Intended to be called exactly once per process at startup.

    Args:
        config: Pre-validated configuration (see
            :mod:`message_service.config.loader`).

    Returns:
        A :class:`Service` ready for request handling.

    Raises:
        ConfigurationError: Tag vocabulary, template manifest, or
            similar file-backed config could not be loaded.
        PersistenceError: SQLite could not be opened, or migrations
            could not be applied.
    """
    # 1. Logging first. Anything raised from here on lands in the
    # structured pipeline.
    configure_logging(
        level=config.observability.log_level,
    )

    _log.info(
        "bootstrap_start",
        sqlite_path=str(config.persistence.sqlite_path),
        pipelines=sorted(config.pipelines.registered),
        log_level=config.observability.log_level,
    )

    # 2. Open the SQLite connection and apply migrations.
    conn: aiosqlite.Connection = await open_connection(config.persistence.sqlite_path)
    try:
        applied = await apply_migrations(conn)
        _log.info(
            "migrations_applied_at_startup",
            count=len(applied),
            versions=[m.version for m in applied],
        )
    except Exception:
        # Close the connection on failure to avoid a leaked fd.
        await conn.close()
        raise

    # 3. Clock. Used by several adapters below and by all use cases.
    clock = SystemClock()

    # 4. Stateless/config-only adapters.
    tag_vocabulary = load_tag_vocabulary(config.tags.vocabulary_path)
    template_repo = load_template_manifest(config.templates.manifest_path)
    template_renderer = Jinja2SandboxedTemplateRenderer(
        repository=template_repo,
        max_context_bytes=config.templates.max_context_bytes,
        max_rendered_bytes=config.templates.max_rendered_bytes,
    )

    # 5. Mailer — pure config, no I/O yet.
    mailer = AiosmtplibMailer(
        host=config.mail.smtp.host,
        port=config.mail.smtp.port,
        username=config.mail.smtp.username,
        password=config.mail.smtp.password,
        use_starttls=config.mail.smtp.use_starttls,
        max_email_size_bytes=config.mail.max_email_size_bytes,
        max_retries=config.mail.retry.max_retries,
        initial_interval_seconds=float(config.mail.retry.initial_interval_seconds),
        max_interval_seconds=float(config.mail.retry.max_interval_seconds),
    )

    # 6. Background-task scheduler.
    scheduler = AsyncioBackgroundTaskScheduler()

    # 7. UoW factory. The repo factories close over the clock (for the
    # subscription repo's ``created_at`` stamping) but not the connection
    # — each UoW gets the connection via the factory and hands it to
    # every repo at ``__aenter__`` time.
    uow_factory = SqliteUnitOfWorkFactory(
        conn=conn,
        run_repo_factory=lambda c: SqliteRunRepository(c),
        stage_repo_factory=lambda c: SqliteStageRepository(c),
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=clock),
        audit_log_factory=lambda c: SqliteAuditLog(c),
    )

    # 8. Use cases. The order between them doesn't matter; each
    # declares its own dependencies.
    email_body_ref = TemplateRef(
        name=config.templates.email_body_template_ref.name,
        version=config.templates.email_body_template_ref.version,
    )
    assemble_and_deliver = AssembleAndDeliverUseCase(
        uow_factory=uow_factory,
        clock=clock,
        template_renderer=template_renderer,
        mailer=mailer,
        from_address=config.mail.from_address,
        email_body_template_ref=email_body_ref,
    )

    begin_run = BeginRunUseCase(
        pipeline_registry=frozenset(config.pipelines.registered),
        tag_vocabulary=tag_vocabulary,
        template_repo=template_repo,
        uow_factory=uow_factory,
        clock=clock,
    )
    submit_stage_report = SubmitStageReportUseCase(
        uow_factory=uow_factory,
        clock=clock,
    )
    finalize_run = FinalizeRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        scheduler=scheduler,
        # When FinalizeRun commits the READY transition, it schedules
        # this factory's return value on the scheduler. The background
        # coroutine is produced fresh per run.
        background_task_factory=lambda run_id: assemble_and_deliver.execute(run_id),
    )

    _log.info("bootstrap_complete")

    return Service(
        config=config,
        clock=clock,
        tag_vocabulary=tag_vocabulary,
        template_repo=template_repo,
        template_renderer=template_renderer,
        mailer=mailer,
        scheduler=scheduler,
        uow_factory=uow_factory,
        begin_run=begin_run,
        submit_stage_report=submit_stage_report,
        finalize_run=finalize_run,
        assemble_and_deliver=assemble_and_deliver,
    )


async def shutdown_service(service: Service, *, timeout: float) -> None:
    """Tear down a :class:`Service` in reverse construction order.

    Order:

    1. Flip the scheduler into shutdown mode so no new background
       tasks can be scheduled.
    2. Await in-flight background tasks up to ``timeout`` seconds;
       cancel stragglers.
    3. Close the UoW factory (releases the SQLite connection).

    Steps (1) and (2) happen before the connection closes so that any
    AssembleAndDeliver task mid-flight can still persist its final
    state transition.

    Args:
        service: The service to tear down.
        timeout: Graceful-drain budget in seconds. Typically
            ``config.service.shutdown_grace_period_seconds``.
    """
    _log.info(
        "shutdown_start",
        active_background_tasks=service.scheduler.active_task_count,
        timeout_seconds=timeout,
    )

    # Phase 1: stop accepting new background work.
    service.scheduler.begin_shutdown()

    # Phase 2: drain in-flight background work.
    await service.scheduler.await_all(timeout=timeout)

    # Phase 3: release the DB connection.
    await service.uow_factory.close()

    _log.info("shutdown_complete")


__all__ = ["Service", "build_service", "shutdown_service"]
