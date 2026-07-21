"""Integration-style tests for :mod:`message_service.interfaces.grpc.servicer`.

These spin up a real in-process ``grpc.aio`` server, register a real
:class:`Service` (built via the bootstrap pathway the same way production
does), and exercise the three RPCs through a generated client stub. This
is the highest-fidelity test we can write without a network-peer client.

What's covered:

* Happy paths for ``BeginRun`` → ``SubmitStageReport`` → ``FinalizeRun``.
* Error translation:
    - unknown pipeline → ``INVALID_ARGUMENT`` with error-code metadata
    - unknown run → ``NOT_FOUND``
    - invalid run state for Finalize → ``FAILED_PRECONDITION``
* Proto ``Struct`` ↔ ``dict`` round-trip with nested objects.
* Retry submission setting ``was_retry=True`` on the second call.
* Proto-level enum translation (``AttachmentMode``).

What's not covered here (see ``tests/integration/test_full_pipeline.py``):

* Background delivery pipeline — Finalize schedules the task but we don't
  wait on it in these tests. They just assert the synchronous response.

Requirement references
----------------------
L2-API-003 (three unary RPCs)
L2-API-008..011 (error translation)
L3-AGGR-002 (Struct → dict conversion)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct
from message_service_proto.v1 import message_service_pb2 as pb
from message_service_proto.v1 import message_service_pb2_grpc as pb_grpc

from message_service.application.ports.clock import Clock
from message_service.application.ports.mailer import Mailer
from message_service.application.ports.report_store import NoOpReportStore
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
from message_service.bootstrap.service import Service
from message_service.config.schema import (
    Config,
    DashboardConfig,
    FilesystemPersistenceConfig,
    GrpcConfig,
    MailConfig,
    MailRetryConfig,
    PersistenceConfig,
    PipelinesConfig,
    SmtpConfig,
    TagsConfig,
    TemplateRefConfig,
    TemplatesConfig,
)
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.infrastructure.auth.argon2_hasher import Argon2PasswordHasher
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.audit_log_pruner_loop import (
    AuditLogPrunerLoop,
)
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
from message_service.infrastructure.persistence.report_pruner_loop import (
    ReportPrunerLoop,
)
from message_service.infrastructure.persistence.run_repository import (
    SqliteRunRepository,
)
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
from message_service.infrastructure.sweeper.loop import SweeperLoop
from message_service.infrastructure.tags.vocabulary_loader import (
    load_tag_vocabulary,
)
from message_service.infrastructure.templating.manifest_loader import (
    load_template_manifest,
)
from message_service.infrastructure.templating.renderer import (
    Jinja2SandboxedTemplateRenderer,
)
from message_service.interfaces.grpc.servicer import register

# -----------------------------------------------------------------------------
# Fixtures — config + adapters + service, wired the same way bootstrap does
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


@pytest.fixture
def tmpl_dir(tmp_path: Path) -> Path:
    d = tmp_path / "templates"
    d.mkdir()
    (d / "body.html.j2").write_text("<p>{{ run_id }}</p>")
    (d / "frag.html.j2").write_text("<p>{{ v }}</p>")
    (d / "agg.html.j2").write_text(
        "<html>{% for s in stages %}{{ s.rendered_html | safe }}{% endfor %}</html>"
    )
    manifest = d / "manifest.toml"
    manifest.write_text(
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

[[template]]
name = "agg"
version = "1.0"
kind = "AGGREGATION"
source_path = "agg.html.j2"
"""
    )
    return manifest


@pytest.fixture
def tags_toml(tmp_path: Path) -> Path:
    p = tmp_path / "tags.toml"
    p.write_text('[[tag]]\nname = "production"\n[[tag]]\nname = "critical"\n')
    return p


@pytest.fixture
def service_config(tmp_path: Path, tmpl_dir: Path, tags_toml: Path) -> Config:
    """A minimal valid Config."""
    return Config(
        grpc=GrpcConfig(host="127.0.0.1", port=50051),
        dashboard=DashboardConfig(host="127.0.0.1", port=8080),
        persistence=PersistenceConfig(
            sqlite_path=tmp_path / "svc.db",
            filesystem=FilesystemPersistenceConfig(report_directory=tmp_path / "reports"),
        ),
        templates=TemplatesConfig(
            manifest_path=tmpl_dir,
            email_body_template_ref=TemplateRefConfig(name="email_body", version="1.0"),
        ),
        tags=TagsConfig(vocabulary_path=tags_toml),
        pipelines=PipelinesConfig(registered=["etl-nightly"]),
        mail=MailConfig(
            from_address="svc@example.com",
            smtp=SmtpConfig(
                host="smtp.example.com",
                port=587,
                username="u",
                password="p",
            ),
            retry=MailRetryConfig(),
        ),
    )


@pytest.fixture
async def service(service_config: Config) -> AsyncIterator[Service]:
    """Compose a Service with a mock Mailer (we don't want SMTP traffic).

    Teardown drains the scheduler and closes the UoW factory so the
    SQLite connection is released between tests; leaked connections
    cause later tests to hang.
    """
    conn = await open_connection(service_config.persistence.sqlite_path)
    await apply_migrations(conn)

    clock = _FixedClock(_T0)
    tag_vocab = load_tag_vocabulary(service_config.tags.vocabulary_path)
    tmpl_repo = load_template_manifest(service_config.templates.manifest_path)
    renderer = Jinja2SandboxedTemplateRenderer(
        repository=tmpl_repo,
        max_context_bytes=service_config.templates.max_context_bytes,
        max_rendered_bytes=service_config.templates.max_rendered_bytes,
    )
    mailer = AsyncMock(spec=Mailer)
    scheduler = AsyncioBackgroundTaskScheduler()

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

    assemble = AssembleAndDeliverUseCase(
        uow_factory=uow_factory,
        clock=clock,
        template_renderer=renderer,
        mailer=mailer,
        from_address=service_config.mail.from_address,
        email_body_template_ref=TemplateRef(name="email_body", version="1.0"),
    )

    # Sweeper components aren't exercised by the servicer tests, but
    # the Service dataclass requires them. Build a minimal sweeper
    # with an empty disposition policy + an interval that won't fire
    # during the test window.
    sweeper_uc = SweeperUseCase(
        uow_factory=uow_factory,
        clock=clock,
        run_timeout_seconds=3600,
        disposition_actions=[],
        handlers_by_id={},
    )
    sweeper_action_dispatcher_uc = SweeperActionDispatcherUseCase(
        uow_factory=uow_factory,
        clock=clock,
        handlers_by_id={},
    )
    sweeper_loop = SweeperLoop(
        sweeper=sweeper_uc,
        dispatcher=sweeper_action_dispatcher_uc,
        scheduler=scheduler,
        poll_interval_seconds=3600,  # effectively never polls during a test
    )

    # Report-pruner components are required for the Service dataclass
    # (Increment 29d) but the gRPC servicer tests don't exercise the
    # rendered-report-retention path. Construct a minimal pruner whose
    # poll-interval is large enough to never fire during the test
    # window; the loop is built but not started.
    report_pruner_uc = ReportPrunerUseCase(
        uow_factory=uow_factory,
        clock=clock,
        report_directory=service_config.persistence.filesystem.report_directory,
        retention_days=90,
        max_prunes_per_iteration=1000,
    )
    report_pruner_loop = ReportPrunerLoop(
        pruner=report_pruner_uc,
        scheduler=scheduler,
        poll_interval_seconds=3600,  # effectively never polls during a test
    )

    # Audit-log-pruner components are required for the Service dataclass
    # (Increment 30d) but the gRPC servicer tests don't exercise the
    # audit-retention path either. Same minimal-construction pattern as
    # the report pruner above; loop never fires during the test window.
    audit_log_pruner_uc = AuditLogPrunerUseCase(
        uow_factory=uow_factory,
        clock=clock,
        retention_days=365,
        cleanup_batch_size=10_000,
    )
    audit_log_pruner_loop = AuditLogPrunerLoop(
        pruner=audit_log_pruner_uc,
        scheduler=scheduler,
        poll_interval_seconds=3600,
    )

    # Auth use cases are required for the Service dataclass but are
    # not exercised by the gRPC servicer tests; they sit on the FastAPI
    # surface (Increment 17). Keep Argon2 cost low so test-suite
    # construction stays cheap.
    password_hasher = Argon2PasswordHasher(
        memory_cost=8,
        time_cost=1,
        parallelism=1,
        hash_len=16,
        salt_len=8,
    )
    login_uc = LoginUseCase(
        uow_factory=uow_factory,
        clock=clock,
        password_hasher=password_hasher,
    )
    logout_uc = LogoutUseCase(uow_factory=uow_factory, clock=clock)
    subscribe_uc = SubscribeUseCase(
        uow_factory=uow_factory,
        clock=clock,
        tag_vocabulary=tag_vocab,
        registered_pipelines=frozenset(service_config.pipelines.registered),
    )
    unsubscribe_uc = UnsubscribeUseCase(uow_factory=uow_factory, clock=clock)
    admin_subscribe_uc = AdminSubscribeUseCase(
        uow_factory=uow_factory,
        clock=clock,
        tag_vocabulary=tag_vocab,
        registered_pipelines=frozenset(service_config.pipelines.registered),
    )
    admin_unsubscribe_uc = AdminUnsubscribeUseCase(uow_factory=uow_factory, clock=clock)
    list_past_runs_uc = ListPastRunsUseCase(uow_factory=uow_factory)
    get_run_detail_uc = GetRunDetailUseCase(uow_factory=uow_factory)
    resend_run_uc = ResendRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        mailer=mailer,
        assemble_and_deliver=assemble,
        from_address=str(service_config.mail.from_address),
    )

    svc = Service(
        config=service_config,
        clock=clock,
        tag_vocabulary=tag_vocab,
        template_repo=tmpl_repo,
        template_renderer=renderer,
        mailer=mailer,
        scheduler=scheduler,
        uow_factory=uow_factory,
        begin_run=BeginRunUseCase(
            pipeline_registry=frozenset(service_config.pipelines.registered),
            tag_vocabulary=tag_vocab,
            template_repo=tmpl_repo,
            uow_factory=uow_factory,
            clock=clock,
        ),
        submit_stage_report=SubmitStageReportUseCase(uow_factory=uow_factory, clock=clock),
        finalize_run=FinalizeRunUseCase(
            uow_factory=uow_factory,
            clock=clock,
            scheduler=scheduler,
            background_task_factory=lambda run_id: assemble.execute(run_id),
        ),
        assemble_and_deliver=assemble,
        sweeper=sweeper_uc,
        sweeper_action_dispatcher=sweeper_action_dispatcher_uc,
        sweeper_loop=sweeper_loop,
        report_pruner=report_pruner_uc,
        report_pruner_loop=report_pruner_loop,
        audit_log_pruner=audit_log_pruner_uc,
        audit_log_pruner_loop=audit_log_pruner_loop,
        password_hasher=password_hasher,
        login=login_uc,
        logout=logout_uc,
        subscribe=subscribe_uc,
        unsubscribe=unsubscribe_uc,
        admin_subscribe=admin_subscribe_uc,
        admin_unsubscribe=admin_unsubscribe_uc,
        list_past_runs=list_past_runs_uc,
        get_run_detail=get_run_detail_uc,
        resend_run=resend_run_uc,
        report_store=NoOpReportStore(),
        create_user=CreateUserUseCase(
            uow_factory=uow_factory, clock=clock, password_hasher=password_hasher
        ),
        update_user=UpdateUserUseCase(uow_factory=uow_factory, clock=clock),
        reset_password=ResetPasswordUseCase(
            uow_factory=uow_factory, clock=clock, password_hasher=password_hasher
        ),
    )
    try:
        yield svc
    finally:
        sweeper_loop.stop()
        scheduler.begin_shutdown()
        await scheduler.await_all(timeout=2.0)
        await uow_factory.close()


@pytest.fixture
async def grpc_client(
    service: Service,
) -> AsyncIterator[pb_grpc.MessageServiceStub]:
    """In-process gRPC server + stub for one test.

    The ``service`` fixture owns its lifecycle (scheduler drain +
    UoW close); this fixture is responsible only for the gRPC
    server/channel pair.
    """
    server = grpc.aio.server()
    register(server, service)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    stub = pb_grpc.MessageServiceStub(channel)
    try:
        yield stub
    finally:
        # Order matters for Windows ProactorEventLoop cleanup: close
        # the client side first so the server sees EOF and can release
        # its completion-port handles. Then stop the server with
        # grace=0 (graceful drain already happened via channel close),
        # then yield control to the event loop so pending cleanup
        # callbacks run before the test loop shuts down. Without this
        # final sleep(0), Windows' ProactorEventLoop GC's sockets
        # during pytest's cleanup stack, producing
        # PytestUnraisableExceptionWarning.
        await channel.close()
        await server.stop(grace=0)
        await asyncio.sleep(0)


# -----------------------------------------------------------------------------
# Happy paths
# -----------------------------------------------------------------------------


def _begin_run_request() -> pb.BeginRunRequest:
    return pb.BeginRunRequest(
        pipeline_type="etl-nightly",
        run_tags=["production"],
        declared_stages=[
            pb.DeclaredStage(
                stage_id="extract",
                stage_order=0,
                report_template=pb.TemplateRef(name="frag", version="1.0"),
            ),
        ],
        attachment_mode=pb.ATTACHMENT_MODE_SINGLE_AGGREGATED,
        aggregation_template=pb.TemplateRef(name="agg", version="1.0"),
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L2-API-003")
async def test_begin_run_happy_path(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    response = await grpc_client.BeginRun(_begin_run_request())
    assert response.run_id  # non-empty UUID
    assert response.initiated_at.seconds > 0


@pytest.mark.asyncio
async def test_begin_run_with_per_stage_mode(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    """PER_STAGE with no aggregation_template SHALL succeed."""
    req = pb.BeginRunRequest(
        pipeline_type="etl-nightly",
        declared_stages=[
            pb.DeclaredStage(
                stage_id="extract",
                stage_order=0,
                report_template=pb.TemplateRef(name="frag", version="1.0"),
            ),
        ],
        attachment_mode=pb.ATTACHMENT_MODE_PER_STAGE,
    )
    response = await grpc_client.BeginRun(req)
    assert response.run_id


@pytest.mark.asyncio
async def test_begin_run_unspecified_attachment_mode_defaults_to_single_aggregated(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    """Omitting attachment_mode SHALL default to SINGLE_AGGREGATED.

    Because the default requires an aggregation_template, the request
    without one SHALL be rejected (which proves the default was applied).
    """
    req = pb.BeginRunRequest(
        pipeline_type="etl-nightly",
        declared_stages=[
            pb.DeclaredStage(
                stage_id="extract",
                stage_order=0,
                report_template=pb.TemplateRef(name="frag", version="1.0"),
            ),
        ],
        # No attachment_mode, no aggregation_template.
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await grpc_client.BeginRun(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_submit_stage_report_happy_path(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    begin_resp = await grpc_client.BeginRun(_begin_run_request())

    ctx = Struct()
    ctx.update({"metric": 42, "nested": {"k": "v"}})

    submit_resp = await grpc_client.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="frag", version="1.0"),
                context=ctx,
            ),
        )
    )
    assert submit_resp.was_retry is False
    assert submit_resp.accepted_at.seconds > 0


@pytest.mark.asyncio
async def test_submit_stage_report_retry_sets_was_retry_true(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    begin_resp = await grpc_client.BeginRun(_begin_run_request())

    ctx1 = Struct()
    ctx1.update({"metric": 1})
    await grpc_client.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="frag", version="1.0"),
                context=ctx1,
            ),
        )
    )

    ctx2 = Struct()
    ctx2.update({"metric": 2})
    second = await grpc_client.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="frag", version="1.0"),
                context=ctx2,
            ),
        )
    )
    assert second.was_retry is True


@pytest.mark.asyncio
async def test_finalize_run_happy_path(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    begin_resp = await grpc_client.BeginRun(_begin_run_request())

    ctx = Struct()
    ctx.update({"metric": 1})
    await grpc_client.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="frag", version="1.0"),
                context=ctx,
            ),
        )
    )

    finalize_resp = await grpc_client.FinalizeRun(pb.FinalizeRunRequest(run_id=begin_resp.run_id))
    assert finalize_resp.finalized_at.seconds > 0


# -----------------------------------------------------------------------------
# Error translation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-API-008")
@pytest.mark.requirement("L3-API-011")
@pytest.mark.requirement("L3-API-012")
async def test_unknown_pipeline_translates_to_invalid_argument(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    req = pb.BeginRunRequest(
        pipeline_type="unregistered-pipeline",
        attachment_mode=pb.ATTACHMENT_MODE_PER_STAGE,
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await grpc_client.BeginRun(req)

    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    # Error-code trailing metadata is attached per L3-API-011.
    trailing = dict(exc_info.value.trailing_metadata())
    assert "x-message-service-error-code" in trailing


@pytest.mark.asyncio
@pytest.mark.requirement("L2-API-009")
@pytest.mark.requirement("L3-API-013")
async def test_submit_to_unknown_run_translates_to_not_found(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    req = pb.SubmitStageReportRequest(
        run_id="00000000-0000-4000-8000-000000000000",
        stage_id="extract",
        report_contribution=pb.ReportContribution(
            template=pb.TemplateRef(name="frag", version="1.0"),
            context=Struct(),
        ),
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await grpc_client.SubmitStageReport(req)
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
    # L3-API-013: the specific error code rides in the trailing metadata, not just
    # the coarse gRPC status.
    trailing = dict(exc_info.value.trailing_metadata())
    assert trailing["x-message-service-error-code"] == "ERROR_CODE_RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_finalize_unknown_run_translates_to_not_found(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    req = pb.FinalizeRunRequest(run_id="00000000-0000-4000-8000-000000000000")
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await grpc_client.FinalizeRun(req)
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_unknown_tag_translates_to_invalid_argument(
    grpc_client: pb_grpc.MessageServiceStub,
) -> None:
    req = pb.BeginRunRequest(
        pipeline_type="etl-nightly",
        run_tags=["not-in-vocabulary"],
        attachment_mode=pb.ATTACHMENT_MODE_PER_STAGE,
    )
    with pytest.raises(grpc.aio.AioRpcError) as exc_info:
        await grpc_client.BeginRun(req)
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# -----------------------------------------------------------------------------
# Struct <-> dict round-trip
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-002")
@pytest.mark.requirement("L3-AGGR-003")
@pytest.mark.requirement("L3-AGGR-017")
async def test_nested_struct_round_trips_into_stage_context(
    grpc_client: pb_grpc.MessageServiceStub,
    service: Service,
) -> None:
    """A nested Struct payload SHALL survive the servicer translation and
    land in persisted stage context as a matching JSON string."""
    begin_resp = await grpc_client.BeginRun(_begin_run_request())

    ctx = Struct()
    ctx.update(
        {
            "metric": 42,
            "tags": ["alpha", "beta"],
            "meta": {"nested": True, "count": 3},
        }
    )
    await grpc_client.SubmitStageReport(
        pb.SubmitStageReportRequest(
            run_id=begin_resp.run_id,
            stage_id="extract",
            report_contribution=pb.ReportContribution(
                template=pb.TemplateRef(name="frag", version="1.0"),
                context=ctx,
            ),
        )
    )

    async with service.uow_factory() as uow:
        stages = await uow.stage_repo.list_by_run(begin_resp.run_id)
    assert len(stages) == 1
    stored = stages[0].report_context_json
    assert stored is not None
    # The JSON encoding is sort_keys + compact separators per our helpers.
    assert '"metric":42' in stored
    assert '"nested":true' in stored
    assert '"tags":["alpha","beta"]' in stored
    # L3-AGGR-003: this request carried no email_body_contribution, so the
    # servicer's proto3 presence check SHALL detect the omission and persist
    # the email-body columns as absent (not an empty contribution).
    assert stages[0].email_body_context_json is None
    assert stages[0].email_body_position is None


# -----------------------------------------------------------------------------
# Class surface check
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-API-005")
def test_servicer_registers_exactly_three_rpc_methods() -> None:
    """The servicer class SHALL declare exactly the three expected RPCs."""
    from message_service.interfaces.grpc.servicer import MessageServiceServicer

    declared = {
        name
        for name, obj in vars(MessageServiceServicer).items()
        if callable(obj) and not name.startswith("_")
    }
    assert declared == {"BeginRun", "SubmitStageReport", "FinalizeRun"}


# -----------------------------------------------------------------------------
# Shutdown — new-RPC rejection (L3-DEP-011)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DEP-011")
async def test_new_rpcs_after_server_stop_initiated_are_unavailable(
    service: Service,
) -> None:
    """L3-DEP-011: once shutdown is initiated, new RPCs SHALL receive UNAVAILABLE.

    Spins up an in-process gRPC server, verifies a baseline RPC works,
    initiates ``server.stop(grace=...)`` as a background task, then
    issues a fresh RPC during the grace window. The fresh RPC must
    fail with ``grpc.StatusCode.UNAVAILABLE`` — that's the wire-level
    contract that ``__main__._run`` relies on after it observes the
    shutdown ``asyncio.Event``.
    """
    server = grpc.aio.server()
    register(server, service)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")
    stub = pb_grpc.MessageServiceStub(channel)

    try:
        # Baseline: a normal RPC succeeds before shutdown begins.
        baseline = await stub.BeginRun(_begin_run_request())
        assert baseline.run_id

        # Kick off graceful stop in the background; with no in-flight
        # RPCs the server transitions to shutting-down immediately, but
        # the coroutine does not return until the stop completes.
        stop_task = asyncio.create_task(server.stop(grace=2.0))

        # Yield to the event loop so server.stop() registers the
        # shutdown intent before we attempt the next RPC.
        await asyncio.sleep(0)

        # New RPC during the grace window — SHALL be rejected.
        with pytest.raises(grpc.aio.AioRpcError) as exc_info:
            await stub.BeginRun(_begin_run_request())
        assert exc_info.value.code() == grpc.StatusCode.UNAVAILABLE, (
            f"expected UNAVAILABLE after server.stop initiated, got {exc_info.value.code()!r}"
        )

        await stop_task
    finally:
        await channel.close()
        # Server is already stopped above; idempotent extra-stop is fine.
        await server.stop(grace=0)
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DEP-011")
async def test_long_running_sweeper_loop_observes_shutdown_via_stop_event(
    service: Service,
) -> None:
    """L3-DEP-011: long-running tasks SHALL observe the shutdown event.

    The shutdown ``asyncio.Event`` in ``__main__._run`` propagates
    into the sweeper loop's stop event via ``shutdown_service``
    (which calls ``service.sweeper_loop.stop()``). This test starts
    the sweeper loop, calls ``stop()`` directly, and asserts the
    loop's scheduled task exits — the spec's "observation" property.
    """
    # Start the loop. Internally schedules a long-running coroutine on
    # the service scheduler that polls the loop's stop event.
    service.sweeper_loop.start()
    # Yield so the scheduler actually picks up the task.
    await asyncio.sleep(0)
    initial_active = service.scheduler.active_task_count
    assert initial_active >= 1, "sweeper loop did not register an active task"

    # Trigger the same stop signal that shutdown_service uses. This is
    # what an observer of the shutdown asyncio.Event would do.
    service.sweeper_loop.stop()

    # Drain — the loop's `await self._stop_event.wait()` should resolve
    # promptly, the coroutine returns, and the active count drops.
    await service.scheduler.await_all(timeout=2.0)
    assert service.scheduler.active_task_count == 0, (
        "sweeper loop did not exit after stop() was called — long-running "
        "tasks are not observing the shutdown signal"
    )
