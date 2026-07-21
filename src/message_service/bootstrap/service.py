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

import contextlib
from dataclasses import dataclass
from pathlib import Path

import aiosqlite
import structlog
from message_service_proto.v1 import message_service_pb2

from message_service.application.ports.clock import Clock
from message_service.application.ports.disposition_handler import DispositionHandler
from message_service.application.ports.mailer import Mailer
from message_service.application.ports.report_store import ReportStore
from message_service.application.ports.template_repository import TemplateRepository
from message_service.application.use_cases.admin_subscriptions import (
    AdminSubscribeUseCase,
    AdminUnsubscribeUseCase,
)
from message_service.application.use_cases.admin_users import (
    CreateUserUseCase,
    ResetPasswordUseCase,
    UpdateUserUseCase,
)
from message_service.application.use_cases.assemble_and_deliver import (
    AssembleAndDeliverUseCase,
)
from message_service.application.use_cases.audit_log_pruner import (
    AuditLogPrunerUseCase,
)
from message_service.application.use_cases.begin_run import BeginRunUseCase
from message_service.application.use_cases.finalize_run import FinalizeRunUseCase
from message_service.application.use_cases.get_run_detail import GetRunDetailUseCase
from message_service.application.use_cases.list_past_runs import ListPastRunsUseCase
from message_service.application.use_cases.login import LoginUseCase
from message_service.application.use_cases.logout import LogoutUseCase
from message_service.application.use_cases.report_pruner import ReportPrunerUseCase
from message_service.application.use_cases.resend_run import ResendRunUseCase
from message_service.application.use_cases.submit_stage_report import (
    SubmitStageReportUseCase,
)
from message_service.application.use_cases.subscribe import SubscribeUseCase
from message_service.application.use_cases.sweeper import SweeperUseCase
from message_service.application.use_cases.sweeper_action_dispatcher import (
    SweeperActionDispatcherUseCase,
)
from message_service.application.use_cases.unsubscribe import UnsubscribeUseCase
from message_service.config.schema import (
    AdminAccountConfig,
    Config,
    DispositionAction,
    TemplateRefConfig,
)
from message_service.domain.aggregates.password import Password
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.aggregates.user import User
from message_service.domain.errors import (
    ConfigurationError,
    assert_error_codes_match_proto_enum,
)
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from message_service.infrastructure.email.aiosmtplib_mailer import AiosmtplibMailer
from message_service.infrastructure.observability.metrics import (
    PrometheusMetricsRecorder,
)
from message_service.infrastructure.persistence.audit_archive_writer import (
    FilesystemAuditArchiveWriter,
)
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.audit_log_pruner_loop import (
    AuditLogPrunerLoop,
)
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.filesystem.report_store import (
    FilesystemReportStore,
)
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.report_pruner_loop import (
    ReportPrunerLoop,
)
from message_service.infrastructure.persistence.run_repository import SqliteRunRepository
from message_service.infrastructure.persistence.session_repository import (
    SqliteSessionRepository,
)
from message_service.infrastructure.persistence.stage_repository import (
    SqliteStageRepository,
)
from message_service.infrastructure.persistence.subscription_repository import (
    SqliteSubscriptionRepository,
)
from message_service.infrastructure.persistence.sweeper_action_repository import (
    SqliteSweeperActionRepository,
)
from message_service.infrastructure.persistence.unit_of_work import (
    SqliteUnitOfWorkFactory,
)
from message_service.infrastructure.persistence.user_repository import (
    SqliteUserRepository,
)
from message_service.infrastructure.scheduler.asyncio_scheduler import (
    AsyncioBackgroundTaskScheduler,
)
from message_service.infrastructure.sweeper.handlers import (
    build_disposition_handler_registry,
)
from message_service.infrastructure.sweeper.loop import SweeperLoop
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


def _ensure_writable_directory(directory: Path, *, label: str) -> None:
    """Create ``directory`` if missing and verify it is writable (create-and-probe).

    Shared by the rendered-report directory (L3-PERS-010/011) and the optional
    audit-archive directory (L3-OBS-041). Both failure modes raise
    :class:`ConfigurationError` so the process exits before any I/O-shaped use
    case runs.

    Args:
        directory: The directory to create/probe.
        label: Human-readable role for the error message (e.g. ``"report"``).

    Raises:
        ConfigurationError: ``mkdir`` failed (permission denied, invalid path,
            etc.) or the directory exists but a probe write fails.
    """
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"failed to create {label} directory: {directory}",
            details={"path": str(directory), "os_error": str(exc)},
        ) from exc

    probe = directory / ".write_probe"
    try:
        probe.write_text("", encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"{label} directory is not writable: {directory}",
            details={"path": str(directory), "os_error": str(exc)},
        ) from exc
    finally:
        # Best-effort cleanup; if we cannot remove the probe the next
        # startup will overwrite it anyway.
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)


def _ensure_report_directory(report_directory: Path) -> None:
    """Verify the rendered-report directory exists and is writable (L3-PERS-010/011)."""
    _ensure_writable_directory(report_directory, label="report")


def _resolve_body_template_overrides(
    overrides: dict[str, TemplateRefConfig],
    repository: TemplateRepository,
) -> dict[str, TemplateRef]:
    """Resolve and validate per-pipeline email-body template overrides (L3-TMPL-034).

    Translates the config ``(name, version)`` refs into :class:`TemplateRef`
    values and asserts each is present in the loaded manifest, honoring
    L1-TMPL-001's "reject references not in the manifest" obligation at
    configuration time so a misconfigured override fails startup rather than
    the first finalized run of the affected pipeline.

    Args:
        overrides: The ``pipelines.email_body_template_overrides`` mapping.
        repository: The template repository loaded from the manifest.

    Returns:
        A mapping of ``pipeline_type`` to the validated :class:`TemplateRef`.

    Raises:
        ConfigurationError: An override references a ``(name, version)`` not
            present in the manifest. The ``details`` carry the offending
            ``pipeline_type``, ``name``, and ``version``.
    """
    resolved: dict[str, TemplateRef] = {}
    for pipeline_type, ref_config in overrides.items():
        ref = TemplateRef(name=ref_config.name, version=ref_config.version)
        if not repository.exists(ref):
            raise ConfigurationError(
                f"email body template override for pipeline {pipeline_type!r} references "
                f"template {ref.name!r}@{ref.version!r}, which is not in the manifest",
                details={
                    "pipeline_type": pipeline_type,
                    "name": ref.name,
                    "version": ref.version,
                },
            )
        resolved[pipeline_type] = ref
    return resolved


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
        sweeper: :class:`SweeperUseCase` — orphan detection +
            outbox enqueue.
        sweeper_action_dispatcher: :class:`SweeperActionDispatcherUseCase`
            — drains the outbox produced by ``sweeper``. Both are
            driven from ``sweeper_loop``.
        sweeper_loop: Periodic loop that calls
            ``sweeper.tick()`` then ``sweeper_action_dispatcher.dispatch_pending()``
            on each tick. Constructed but not started; the CLI
            entrypoint (or tests) calls ``start()``.
        report_pruner: :class:`ReportPrunerUseCase` — rendered-report
            retention pruner per L1-PERS-004. Driven from
            ``report_pruner_loop`` in production; tests can drive
            ``run_once()`` synchronously for deterministic
            sequencing (same pattern as the orphan-path e2e test).
        report_pruner_loop: Periodic loop that calls
            ``report_pruner.run_once()`` at the configured cadence.
            Constructed but not started; the CLI entrypoint (or
            tests) calls ``start()``. Same start-after-bind /
            stop-in-grace-period lifecycle as ``sweeper_loop``.
        audit_log_pruner: :class:`AuditLogPrunerUseCase` —
            audit-log retention pruner per L1-OBS-003 / L2-OBS-008
            / L2-OBS-009. Driven from ``audit_log_pruner_loop`` in
            production; tests can drive ``run_once()`` synchronously
            for deterministic sequencing (same pattern as the
            orphan-path e2e and report-pruner tests).
        audit_log_pruner_loop: Periodic loop that calls
            ``audit_log_pruner.run_once()`` at the
            ``cleanup_interval_hours`` cadence. Same lifecycle
            pattern as ``sweeper_loop`` and ``report_pruner_loop``.
    """

    config: Config
    clock: Clock
    tag_vocabulary: InMemoryTagVocabulary
    template_repo: InMemoryTemplateRepository
    template_renderer: Jinja2SandboxedTemplateRenderer
    mailer: Mailer
    scheduler: AsyncioBackgroundTaskScheduler
    uow_factory: SqliteUnitOfWorkFactory
    report_store: ReportStore
    begin_run: BeginRunUseCase
    submit_stage_report: SubmitStageReportUseCase
    finalize_run: FinalizeRunUseCase
    assemble_and_deliver: AssembleAndDeliverUseCase
    sweeper: SweeperUseCase
    sweeper_action_dispatcher: SweeperActionDispatcherUseCase
    sweeper_loop: SweeperLoop
    report_pruner: ReportPrunerUseCase
    report_pruner_loop: ReportPrunerLoop
    audit_log_pruner: AuditLogPrunerUseCase
    audit_log_pruner_loop: AuditLogPrunerLoop
    password_hasher: Argon2PasswordHasher
    login: LoginUseCase
    logout: LogoutUseCase
    subscribe: SubscribeUseCase
    unsubscribe: UnsubscribeUseCase
    admin_subscribe: AdminSubscribeUseCase
    admin_unsubscribe: AdminUnsubscribeUseCase
    list_past_runs: ListPastRunsUseCase
    get_run_detail: GetRunDetailUseCase
    resend_run: ResendRunUseCase
    create_user: CreateUserUseCase
    update_user: UpdateUserUseCase
    reset_password: ResetPasswordUseCase


async def _reconcile_admin_account(
    admin: AdminAccountConfig,
    *,
    uow_factory: SqliteUnitOfWorkFactory,
    password_hasher: Argon2PasswordHasher,
    clock: Clock,
) -> None:
    """Provision the configurable local admin at startup (L2-AUTH-011 / L3-AUTH-019).

    Fail-safe reconciliation against the account store, keyed by the configured
    email: create the account if absent (with the hashed configured password,
    ``is_admin=True``, ``disabled=False``); if it already exists, re-assert
    administrator privilege and enabled status but leave the stored password
    untouched (so a password rotated through the admin API is never clobbered
    by a stale config value). Idempotent across restarts. Uses the repository
    directly — this is a system-startup action, not an admin action, so it emits
    no audit record and is not subject to the self-protection guards.

    Args:
        admin: The configured admin account (email + plaintext secret).
        uow_factory: Unit-of-work factory over the shared connection.
        password_hasher: The Argon2id chokepoint (L1-AUTH-001).
        clock: Injected clock for the created-at stamp.
    """
    async with uow_factory() as uow:
        existing = await uow.user_repo.get_by_email(admin.email)
        if existing is None:
            await uow.user_repo.save(
                User(
                    email=admin.email,
                    display_name=admin.email,
                    password_hash=password_hasher.hash(Password(admin.password)),
                    created_at=clock.now(),
                    is_admin=True,
                    disabled=False,
                )
            )
            created = True
        else:
            if existing.user_id is None:  # invariant: looked-up account is persisted
                raise RuntimeError("existing admin account is missing a persisted user_id")
            await uow.user_repo.update(existing.user_id, is_admin=True, disabled=False)
            created = False
        await uow.commit()
    _log.info("admin_account_reconciled", email=admin.email, created=created)


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

    # 1a. Error-code self-check (L3-ERR-008, L3-ERR-009). Verify
    # every concrete exception class's `error_code` ClassVar exists
    # in the proto ErrorCode enum, and warn on proto values that no
    # Python class exposes (the latter is non-fatal — proto may
    # legitimately declare codes ahead of their Python counterparts
    # during phased rollouts).
    proto_error_codes = set(message_service_pb2.ErrorCode.keys())
    orphan_proto_codes = assert_error_codes_match_proto_enum(proto_error_codes)
    if orphan_proto_codes:
        _log.warning(
            "proto_error_codes_without_python_class",
            orphan_codes=orphan_proto_codes,
        )

    # 2. Open the SQLite connection, then assemble everything else under a
    # guard that closes the connection on ANY startup failure. Previously only
    # a migration failure closed it; a failure in any later step (tag vocab,
    # template manifest, report-dir probe, mailer params, disposition-handler
    # validation, admin provisioning, …) left the connection open — leaking an
    # fd plus aiosqlite's background thread, which under repeated construction
    # (tests) surfaces as a ResourceWarning. On success the connection's
    # lifecycle transfers to the UoW factory (closed by shutdown_service via
    # uow_factory.close()). The assembly is delegated to _assemble_service so
    # the guard can wrap the whole construction without deeply nesting it.
    # A ``try/finally`` with a success flag closes the connection on ANY failure
    # (including BaseException — a cancelled or interrupted startup) without an
    # ``except BaseException`` clause, which the L3-ERR-021 chokepoint discipline
    # reserves for the gRPC translator.
    conn: aiosqlite.Connection = await open_connection(config.persistence.sqlite_path)
    assembled = False
    try:
        service = await _assemble_service(config, conn)
        assembled = True
        return service
    finally:
        if not assembled:
            await conn.close()


async def _assemble_service(config: Config, conn: aiosqlite.Connection) -> Service:
    """Apply migrations and construct the fully-wired :class:`Service`.

    Extracted from :func:`build_service` so the caller can guard the entire
    post-connection assembly with a single ``try/except`` that closes ``conn``
    on any failure (connection-leak safety). Called exactly once per build.

    Args:
        config: Pre-validated configuration.
        conn: The open SQLite connection whose ownership this assembly takes
            (on success it is handed to the returned service's UoW factory).

    Returns:
        The assembled :class:`Service`.
    """
    # 2a. Apply migrations against the open connection.
    applied = await apply_migrations(conn)
    _log.info(
        "migrations_applied_at_startup",
        count=len(applied),
        versions=[m.version for m in applied],
    )

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

    # 4b. Filesystem report store (L1-PERS-002 / L3-PERS-010 / L3-PERS-011).
    # Create the configured root if missing, then probe-write a small
    # file to verify the directory is writable. Both failures are
    # surfaced as ``ConfigurationError`` so the process exits with a
    # nonzero status before any UoW or use case is constructed.
    _ensure_report_directory(config.persistence.filesystem.report_directory)
    report_store = FilesystemReportStore(
        root=config.persistence.filesystem.report_directory,
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
        sweeper_action_repo_factory=lambda c: SqliteSweeperActionRepository(c),
        user_repo_factory=lambda c: SqliteUserRepository(c),
        session_repo_factory=lambda c: SqliteSessionRepository(c),
    )

    # 8. Use cases. The order between them doesn't matter; each
    # declares its own dependencies.
    # L1-OBS-002: a single PrometheusMetricsRecorder is shared by every
    # use case that emits metrics. Wraps the module-level prometheus_client
    # singletons in infrastructure/observability/metrics.py.
    metrics_recorder = PrometheusMetricsRecorder()
    email_body_ref = TemplateRef(
        name=config.templates.email_body_template_ref.name,
        version=config.templates.email_body_template_ref.version,
    )
    # L3-TMPL-034: validate per-pipeline email-body overrides against the
    # loaded manifest here (fail-fast) before any use case is constructed.
    email_body_template_overrides = _resolve_body_template_overrides(
        dict(config.pipelines.email_body_template_overrides),
        template_repo,
    )
    assemble_and_deliver = AssembleAndDeliverUseCase(
        uow_factory=uow_factory,
        clock=clock,
        template_renderer=template_renderer,
        mailer=mailer,
        from_address=config.mail.from_address,
        email_body_template_ref=email_body_ref,
        admin_recipients=tuple(str(addr) for addr in config.mail.admin_recipients),
        subject_templates=dict(config.pipelines.subject_templates),
        email_body_template_overrides=email_body_template_overrides,
        metrics_recorder=metrics_recorder,
        report_store=report_store,
    )

    begin_run = BeginRunUseCase(
        pipeline_registry=frozenset(config.pipelines.registered),
        tag_vocabulary=tag_vocabulary,
        template_repo=template_repo,
        uow_factory=uow_factory,
        clock=clock,
        metrics_recorder=metrics_recorder,
    )
    submit_stage_report = SubmitStageReportUseCase(
        uow_factory=uow_factory,
        clock=clock,
        metrics_recorder=metrics_recorder,
    )
    finalize_run = FinalizeRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        scheduler=scheduler,
        # When FinalizeRun commits the READY transition, it schedules
        # this factory's return value on the scheduler. The background
        # coroutine is produced fresh per run.
        background_task_factory=lambda run_id: assemble_and_deliver.execute(run_id),
        metrics_recorder=metrics_recorder,
    )

    # 9. Sweeper. The registry from infrastructure/sweeper/handlers.py
    # lists only the action ids whose handlers are actually implemented;
    # ``SEND_PARTIAL_FLAGGED`` and ``NOTIFY_SUBSCRIBERS`` are still valid
    # identifiers in the ``DispositionAction`` literal but referencing
    # them in ``config.sweeper.disposition_actions`` causes the
    # ``SweeperUseCase`` constructor to raise ``ConfigurationError`` at
    # startup. That fail-loud-early posture replaces the previous
    # placeholder handlers that raised ``NotImplementedError`` per
    # orphan at runtime.
    handlers_by_id: dict[DispositionAction, DispositionHandler] = (
        build_disposition_handler_registry()
    )
    sweeper = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=config.sweeper.run_timeout_seconds,
        disposition_actions=config.sweeper.disposition_actions,
        handlers_by_id=handlers_by_id,
        disposition_overrides=config.pipelines.orphan_disposition_overrides,
        max_candidates_per_iteration=config.sweeper.max_candidates_per_iteration,
        metrics_recorder=metrics_recorder,
    )
    sweeper_action_dispatcher = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id=handlers_by_id,
        stale_claim_threshold_seconds=config.sweeper.stale_claim_threshold_seconds,
        max_dispatch_attempts=config.sweeper.max_dispatch_attempts,
    )
    sweeper_loop = SweeperLoop(
        sweeper=sweeper,
        dispatcher=sweeper_action_dispatcher,
        scheduler=scheduler,
        poll_interval_seconds=config.sweeper.poll_interval_seconds,
    )
    # NOTE: the sweeper loop is constructed but NOT started here.
    # The CLI entrypoint (or tests that want the loop running) calls
    # ``service.sweeper_loop.start()`` explicitly. The L2-PERS-004
    # mutex (Increment 27) makes the inline start safe against the
    # historical "cannot start a transaction within a transaction"
    # race, but the CLI-driven start ordering is preserved as a
    # readability convention so request listeners are bound before
    # any background polling begins.

    # 9b. Rendered-report retention pruner (L1-PERS-004; Increment 29).
    # Constructed alongside the sweeper because it shares the
    # BackgroundTaskScheduler + UoW-mutex story. NOT started here —
    # same CLI-driven lifecycle as ``sweeper_loop`` per L3-PERS-030.
    report_pruner = ReportPrunerUseCase(
        uow_factory=uow_factory,
        clock=clock,
        report_directory=config.persistence.filesystem.report_directory,
        retention_days=config.persistence.filesystem.report_retention_days,
        max_prunes_per_iteration=config.persistence.filesystem.max_prunes_per_iteration,
    )
    report_pruner_loop = ReportPrunerLoop(
        pruner=report_pruner,
        scheduler=scheduler,
        poll_interval_seconds=config.persistence.filesystem.prune_interval_seconds,
    )

    # 9c. Audit-log retention pruner (L1-OBS-003; Increment 30).
    # Same BackgroundTaskScheduler-driven pattern as 9b's report
    # pruner. NOT started here — same CLI-driven lifecycle. Per
    # L3-OBS-040 the pruner does not audit its own delete activity;
    # the structured INFO log per tick is the operational signal,
    # and the L3-OBS-039 sole-deleter conformance test provides
    # forensic-grade auditability.
    # L2-OBS-019: optional audit-archive writer. When archive_directory is
    # configured, validate it writable (fail-fast) and archive expired rows
    # before the pruner deletes them; unset means delete without archiving.
    audit_archive_writer: FilesystemAuditArchiveWriter | None = None
    audit_archive_dir = config.observability.audit.archive_directory
    if audit_archive_dir is not None:
        _ensure_writable_directory(audit_archive_dir, label="audit archive")
        audit_archive_writer = FilesystemAuditArchiveWriter(root=audit_archive_dir)
    audit_log_pruner = AuditLogPrunerUseCase(
        uow_factory=uow_factory,
        clock=clock,
        retention_days=config.observability.audit.retention_days,
        cleanup_batch_size=config.observability.audit.cleanup_batch_size,
        archive_writer=audit_archive_writer,
    )
    audit_log_pruner_loop = AuditLogPrunerLoop(
        pruner=audit_log_pruner,
        scheduler=scheduler,
        poll_interval_seconds=config.observability.audit.cleanup_interval_hours * 3600,
    )

    # 10. Auth (Increment 16). The Argon2 hasher is a service-scoped
    # singleton (L3-AUTH-001) sourced from the auth.argon2.* config keys.
    password_hasher = Argon2PasswordHasher(
        memory_cost=config.auth.argon2.memory_cost,
        time_cost=config.auth.argon2.time_cost,
        parallelism=config.auth.argon2.parallelism,
        hash_len=config.auth.argon2.hash_len,
        salt_len=config.auth.argon2.salt_len,
    )
    login = LoginUseCase(
        uow_factory=uow_factory,
        clock=clock,
        password_hasher=password_hasher,
    )
    logout = LogoutUseCase(uow_factory=uow_factory, clock=clock)
    subscribe = SubscribeUseCase(
        uow_factory=uow_factory,
        clock=clock,
        tag_vocabulary=tag_vocabulary,
        registered_pipelines=frozenset(config.pipelines.registered),
    )
    unsubscribe = UnsubscribeUseCase(uow_factory=uow_factory, clock=clock)
    admin_subscribe = AdminSubscribeUseCase(
        uow_factory=uow_factory,
        clock=clock,
        tag_vocabulary=tag_vocabulary,
        registered_pipelines=frozenset(config.pipelines.registered),
    )
    admin_unsubscribe = AdminUnsubscribeUseCase(uow_factory=uow_factory, clock=clock)
    list_past_runs = ListPastRunsUseCase(uow_factory=uow_factory)
    get_run_detail = GetRunDetailUseCase(uow_factory=uow_factory)
    resend_run = ResendRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        mailer=mailer,
        assemble_and_deliver=assemble_and_deliver,
        from_address=str(config.mail.from_address),
    )

    # Increment 20b: admin user-management use cases. All three share
    # the password_hasher singleton (L3-AUTH-016 chokepoint) with
    # LoginUseCase so admin-set and self-set passwords obey identical
    # Argon2id discipline.
    create_user = CreateUserUseCase(
        uow_factory=uow_factory,
        clock=clock,
        password_hasher=password_hasher,
    )
    update_user = UpdateUserUseCase(uow_factory=uow_factory, clock=clock)
    reset_password = ResetPasswordUseCase(
        uow_factory=uow_factory,
        clock=clock,
        password_hasher=password_hasher,
    )

    # L2-AUTH-011: provision the configurable local admin before the listeners
    # accept traffic, so the operator can authenticate on the very first request.
    # No-op when [auth.admin] is absent (backward compatible).
    if config.auth.admin is not None:
        await _reconcile_admin_account(
            config.auth.admin,
            uow_factory=uow_factory,
            password_hasher=password_hasher,
            clock=clock,
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
        report_store=report_store,
        begin_run=begin_run,
        submit_stage_report=submit_stage_report,
        finalize_run=finalize_run,
        assemble_and_deliver=assemble_and_deliver,
        sweeper=sweeper,
        sweeper_action_dispatcher=sweeper_action_dispatcher,
        sweeper_loop=sweeper_loop,
        report_pruner=report_pruner,
        report_pruner_loop=report_pruner_loop,
        audit_log_pruner=audit_log_pruner,
        audit_log_pruner_loop=audit_log_pruner_loop,
        password_hasher=password_hasher,
        login=login,
        logout=logout,
        subscribe=subscribe,
        unsubscribe=unsubscribe,
        admin_subscribe=admin_subscribe,
        admin_unsubscribe=admin_unsubscribe,
        list_past_runs=list_past_runs,
        get_run_detail=get_run_detail,
        resend_run=resend_run,
        create_user=create_user,
        update_user=update_user,
        reset_password=reset_password,
    )


async def shutdown_service(service: Service, *, timeout: float) -> None:
    """Tear down a :class:`Service` in reverse construction order.

    Order:

    1. Signal the sweeper loop AND the report-pruner loop to exit at
       the next iteration boundary.
    2. Flip the scheduler into shutdown mode so no new background
       tasks can be scheduled.
    3. Await in-flight background tasks up to ``timeout`` seconds;
       cancel stragglers.
    4. Close the UoW factory (releases the SQLite connection).

    Steps (1)-(3) happen before the connection closes so that any
    AssembleAndDeliver task mid-flight can still persist its final
    state transition. Step (1) gives the periodic loops a chance to
    exit cleanly rather than via :class:`asyncio.CancelledError` from
    the scheduler's ``await_all`` timeout.

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

    # Phase 1: signal the periodic loops to exit cleanly.
    service.sweeper_loop.stop()
    service.report_pruner_loop.stop()
    service.audit_log_pruner_loop.stop()

    # Phase 2: stop accepting new background work.
    service.scheduler.begin_shutdown()

    # Phase 3: drain in-flight background work.
    await service.scheduler.await_all(timeout=timeout)

    # Phase 3: release the DB connection.
    await service.uow_factory.close()

    _log.info("shutdown_complete")


__all__ = ["Service", "build_service", "shutdown_service"]
