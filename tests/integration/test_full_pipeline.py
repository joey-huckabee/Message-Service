"""End-to-end integration: BeginRun → SubmitStageReport → FinalizeRun → AssembleAndDeliver.

Wires together every port adapter built through Increment 11c:

* :class:`SqliteUnitOfWorkFactory` holding real :class:`SqliteRunRepository`,
  :class:`SqliteStageRepository`, :class:`SqliteSubscriptionRepository`,
  and :class:`SqliteAuditLog` instances.
* :class:`InMemoryTagVocabulary` and :class:`InMemoryTemplateRepository`
  built from in-memory TOML payloads on disk.
* :class:`Jinja2SandboxedTemplateRenderer` rendering real Jinja2
  templates.
* :class:`AsyncioBackgroundTaskScheduler` running the background
  AssembleAndDeliver coroutine, with :meth:`await_all` flushing before
  assertions.
* Mock :class:`Mailer` (we do not connect to an SMTP server in tests).

These tests are our first proof that the ports, adapters, and use
cases compose correctly against real infrastructure. If any port
contract diverged from its adapter, or an aggregate invariant drifted
from the schema, we'd find out here.

Requirement references
----------------------
L1-RUN-001 through L1-RUN-004 (full lifecycle)
L1-AGGR-002, L1-AGGR-003 (attachment assembly)
L1-SUB-004 (recipient resolution)
L1-MAIL-001 (SMTP delivery invoked)
L2-RUN-003 (transactional audit+state)
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.ports.mailer import Mailer
from message_service.application.use_cases.assemble_and_deliver import (
    AssembleAndDeliverUseCase,
)
from message_service.application.use_cases.begin_run import BeginRunUseCase
from message_service.application.use_cases.begin_run_command import (
    BeginRunCommand,
    DeclaredStageInput,
)
from message_service.application.use_cases.finalize_run import FinalizeRunUseCase
from message_service.application.use_cases.finalize_run_command import (
    FinalizeRunCommand,
)
from message_service.application.use_cases.submit_stage_report import (
    SubmitStageReportUseCase,
)
from message_service.application.use_cases.submit_stage_report_command import (
    SubmitStageReportCommand,
)
from message_service.domain.aggregates.audit_event import AuditAction
from message_service.domain.aggregates.run import AttachmentMode
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import EmailDeliveryError
from message_service.domain.ids import RunId
from message_service.domain.state_machines.run_states import RunState
from message_service.infrastructure.persistence.audit_log import SqliteAuditLog
from message_service.infrastructure.persistence.connection import open_connection
from message_service.infrastructure.persistence.migration_runner import apply_migrations
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

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    """Deterministic clock — each ``now()`` call returns the same value."""

    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._value += timedelta(seconds=seconds)


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    """Fresh migrated SQLite :memory: database; closed on teardown."""
    conn = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(conn)
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def clock() -> _FixedClock:
    return _FixedClock()


@pytest.fixture
def tag_vocab(tmp_path: Path) -> InMemoryTagVocabulary:
    """Tag vocabulary with ``production`` and ``critical``."""
    p = tmp_path / "tags.toml"
    p.write_text(
        """
[[tag]]
name = "production"

[[tag]]
name = "critical"
"""
    )
    return load_tag_vocabulary(p)


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    """Create Jinja2 templates on disk and return the manifest path's parent."""
    d = tmp_path / "templates"
    d.mkdir()
    (d / "extract_report.html.j2").write_text("<p>Extract: {{ metric }}</p>")
    (d / "transform_report.html.j2").write_text("<p>Transform: {{ row_count }}</p>")
    (d / "aggregation.html.j2").write_text(
        "<html><body>"
        "<h1>Run {{ pipeline_type }}</h1>"
        "{% for s in stages %}<div>{{ s.rendered_html | safe }}</div>{% endfor %}"
        "</body></html>"
    )
    (d / "email_body.html.j2").write_text(
        "<html><body>"
        "<p>Run {{ run_id }} ({{ pipeline_type }}) complete.</p>"
        "<ul>{% for s in stages %}<li>{{ s.stage_id }}</li>{% endfor %}</ul>"
        "</body></html>"
    )

    manifest = d / "manifest.toml"
    manifest.write_text(
        """
[[template]]
name = "extract_report"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "extract_report.html.j2"

[[template]]
name = "transform_report"
version = "1.0"
kind = "REPORT_FRAGMENT"
source_path = "transform_report.html.j2"

[[template]]
name = "aggregation"
version = "1.0"
kind = "AGGREGATION"
source_path = "aggregation.html.j2"

[[template]]
name = "email_body"
version = "1.0"
kind = "EMAIL_BODY"
source_path = "email_body.html.j2"
"""
    )
    return manifest


@pytest.fixture
def template_repo(templates_dir: Path) -> InMemoryTemplateRepository:
    return load_template_manifest(templates_dir)


@pytest.fixture
def template_renderer(
    template_repo: InMemoryTemplateRepository,
) -> Jinja2SandboxedTemplateRenderer:
    return Jinja2SandboxedTemplateRenderer(
        repository=template_repo,
        max_context_bytes=1_000_000,
        max_rendered_bytes=10_000_000,
    )


@pytest.fixture
def uow_factory(sqlite_conn: aiosqlite.Connection, clock: _FixedClock) -> SqliteUnitOfWorkFactory:
    return SqliteUnitOfWorkFactory(
        conn=sqlite_conn,
        run_repo_factory=lambda c: SqliteRunRepository(c),
        stage_repo_factory=lambda c: SqliteStageRepository(c),
        subscription_repo_factory=lambda c: SqliteSubscriptionRepository(c, clock=clock),
        audit_log_factory=lambda c: SqliteAuditLog(c),
        sweeper_action_repo_factory=lambda c: SqliteSweeperActionRepository(c),
        user_repo_factory=lambda c: SqliteUserRepository(c),
        session_repo_factory=lambda c: SqliteSessionRepository(c),
    )


@pytest.fixture
def mailer() -> AsyncMock:
    return AsyncMock(spec=Mailer)


@pytest.fixture
def scheduler() -> AsyncioBackgroundTaskScheduler:
    return AsyncioBackgroundTaskScheduler()


# -----------------------------------------------------------------------------
# Helper — wire up all four use cases
# -----------------------------------------------------------------------------


def _build_use_cases(
    *,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> tuple[
    BeginRunUseCase,
    SubmitStageReportUseCase,
    FinalizeRunUseCase,
    AssembleAndDeliverUseCase,
]:
    begin = BeginRunUseCase(
        pipeline_registry=frozenset({"etl-nightly"}),
        tag_vocabulary=tag_vocab,
        template_repo=template_repo,
        uow_factory=uow_factory,
        clock=clock,
    )
    submit = SubmitStageReportUseCase(
        uow_factory=uow_factory,
        clock=clock,
    )
    assemble = AssembleAndDeliverUseCase(
        uow_factory=uow_factory,
        clock=clock,
        template_renderer=template_renderer,
        mailer=mailer,
        from_address="svc@example.com",
        email_body_template_ref=TemplateRef(name="email_body", version="1.0"),
    )
    finalize = FinalizeRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        scheduler=scheduler,
        background_task_factory=lambda run_id: assemble.execute(run_id),
    )
    return begin, submit, finalize, assemble


async def _seed_subscriber(
    conn: aiosqlite.Connection,
    email: str,
    granularity: SubscriptionGranularity,
    target_value: str | None,
    *,
    disabled: int = 0,
) -> None:
    """Insert a user and a subscription for that user."""
    cur = await conn.execute(
        "INSERT INTO users (email, display_name, disabled, created_at) VALUES (?, ?, ?, ?)",
        (email, "Test", disabled, "2026-04-21T00:00:00Z"),
    )
    user_id = cur.lastrowid
    await conn.execute(
        "INSERT INTO subscriptions (user_id, granularity, target_value, created_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, granularity.value, target_value, "2026-04-21T00:00:00Z"),
    )
    await conn.commit()


# -----------------------------------------------------------------------------
# Happy path — full lifecycle with delivery
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-004")
async def test_full_lifecycle_ends_with_sent_and_mailer_called(
    sqlite_conn: aiosqlite.Connection,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """BeginRun → Submit x2 → Finalize → drain scheduler → assert SENT."""
    # Seed a subscriber so recipient resolution yields a non-empty set.
    await _seed_subscriber(sqlite_conn, "alice@example.com", SubscriptionGranularity.GLOBAL, None)

    begin, submit, finalize, _ = _build_use_cases(
        uow_factory=uow_factory,
        clock=clock,
        template_repo=template_repo,
        template_renderer=template_renderer,
        tag_vocab=tag_vocab,
        mailer=mailer,
        scheduler=scheduler,
    )

    # BeginRun
    run_id = await begin.execute(
        BeginRunCommand(
            pipeline_type="etl-nightly",
            tags=frozenset({"production", "critical"}),
            declared_stages=(
                DeclaredStageInput(
                    stage_id="extract",
                    stage_order=0,
                    report_template_ref=TemplateRef(name="extract_report", version="1.0"),
                ),
                DeclaredStageInput(
                    stage_id="transform",
                    stage_order=1,
                    report_template_ref=TemplateRef(name="transform_report", version="1.0"),
                ),
            ),
            attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
            aggregation_template_ref=TemplateRef(name="aggregation", version="1.0"),
        )
    )

    # Verify Run and stages persisted in INITIATED / PENDING states
    async with uow_factory() as uow:
        run = await uow.run_repo.get(run_id)
        assert run.state is RunState.INITIATED
        stages = await uow.stage_repo.list_by_run(run_id)
        assert len(stages) == 2

    # Submit both stages
    await submit.execute(
        SubmitStageReportCommand(
            run_id=run_id,
            stage_id="extract",
            report_context={"metric": 42},
        )
    )
    await submit.execute(
        SubmitStageReportCommand(
            run_id=run_id,
            stage_id="transform",
            report_context={"row_count": 10_000},
        )
    )

    # After both submissions, Run SHALL be AGGREGATING
    async with uow_factory() as uow:
        run = await uow.run_repo.get(run_id)
        assert run.state is RunState.AGGREGATING

    # Finalize — schedules AssembleAndDeliver in the background
    result = await finalize.execute(FinalizeRunCommand(run_id=run_id))
    assert result.run_id == run_id

    # Drain the scheduler; AssembleAndDeliver runs to completion.
    await scheduler.await_all(timeout=5.0)

    # Run is SENT
    async with uow_factory() as uow:
        run = await uow.run_repo.get(run_id)
        assert run.state is RunState.SENT

    # Mailer was called exactly once with an OutboundEmail
    mailer.send.assert_awaited_once()
    email = mailer.send.call_args.args[0]
    assert email.recipients == frozenset({"alice@example.com"})
    assert email.from_address == "svc@example.com"
    assert len(email.attachments) == 1  # SINGLE_AGGREGATED → one attachment
    # The aggregated HTML includes content rendered from each stage.
    agg_content = email.attachments[0].content.decode("utf-8")
    assert "Extract: 42" in agg_content
    assert "Transform: 10000" in agg_content


# -----------------------------------------------------------------------------
# Zero subscribers — SENT with no mailer call
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
async def test_zero_subscribers_reaches_sent_without_mailer_call(
    sqlite_conn: aiosqlite.Connection,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """No subscribers match → run finalizes SENT with recipient_count=0."""
    # No subscribers seeded.
    begin, submit, finalize, _ = _build_use_cases(
        uow_factory=uow_factory,
        clock=clock,
        template_repo=template_repo,
        template_renderer=template_renderer,
        tag_vocab=tag_vocab,
        mailer=mailer,
        scheduler=scheduler,
    )

    run_id = await begin.execute(
        BeginRunCommand(
            pipeline_type="etl-nightly",
            tags=frozenset({"production"}),
            declared_stages=(
                DeclaredStageInput(
                    stage_id="extract",
                    stage_order=0,
                    report_template_ref=TemplateRef(name="extract_report", version="1.0"),
                ),
            ),
            attachment_mode=AttachmentMode.PER_STAGE,
        )
    )
    await submit.execute(
        SubmitStageReportCommand(
            run_id=run_id,
            stage_id="extract",
            report_context={"metric": 1},
        )
    )
    await finalize.execute(FinalizeRunCommand(run_id=run_id))
    await scheduler.await_all(timeout=5.0)

    async with uow_factory() as uow:
        run = await uow.run_repo.get(run_id)
        assert run.state is RunState.SENT

    # Mailer NOT called — zero recipients short-circuit.
    mailer.send.assert_not_called()


# -----------------------------------------------------------------------------
# Mailer failure — ends in FAILED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-MAIL-001")
async def test_mailer_failure_ends_run_in_failed(
    sqlite_conn: aiosqlite.Connection,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """Mailer raises EmailDeliveryError → run transitions to FAILED with structured audit."""
    await _seed_subscriber(sqlite_conn, "alice@example.com", SubscriptionGranularity.GLOBAL, None)

    mailer.send.side_effect = EmailDeliveryError(
        "SMTP 550", details={"failure_reason": "PERMANENT_SMTP_FAILURE"}
    )

    begin, submit, finalize, _ = _build_use_cases(
        uow_factory=uow_factory,
        clock=clock,
        template_repo=template_repo,
        template_renderer=template_renderer,
        tag_vocab=tag_vocab,
        mailer=mailer,
        scheduler=scheduler,
    )

    run_id = await begin.execute(
        BeginRunCommand(
            pipeline_type="etl-nightly",
            tags=frozenset({"production"}),
            declared_stages=(
                DeclaredStageInput(
                    stage_id="extract",
                    stage_order=0,
                    report_template_ref=TemplateRef(name="extract_report", version="1.0"),
                ),
            ),
            attachment_mode=AttachmentMode.PER_STAGE,
        )
    )
    await submit.execute(
        SubmitStageReportCommand(
            run_id=run_id,
            stage_id="extract",
            report_context={"metric": 1},
        )
    )
    await finalize.execute(FinalizeRunCommand(run_id=run_id))
    await scheduler.await_all(timeout=5.0)

    async with uow_factory() as uow:
        run = await uow.run_repo.get(run_id)
        assert run.state is RunState.FAILED

        # Audit log recorded the failure. The run-level ``failure_reason``
        # stays within the ``L3-RUN-029`` closed vocabulary
        # (``EMAIL_DELIVERY`` for an ``EmailDeliveryError``); the mailer's
        # SMTP-level classification (``PERMANENT_SMTP_FAILURE``) is preserved
        # under a separate ``smtp_failure_classification`` key rather than
        # overwriting the run failure_reason (see ``L3-MAIL-008``).
        send_events = await uow.audit_log.query(action=AuditAction.SEND_REPORT)
        assert len(send_events) == 1
        failure = send_events[0]
        assert failure.details["failure_reason"] == "EMAIL_DELIVERY"
        assert failure.details["smtp_failure_classification"] == "PERMANENT_SMTP_FAILURE"
        assert failure.outcome.value == "FAILURE"


# -----------------------------------------------------------------------------
# Audit trail — verify the full lifecycle's audit log
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-OBS-003")
async def test_full_lifecycle_audit_trail_contains_all_events(
    sqlite_conn: aiosqlite.Connection,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    """Every state transition SHALL produce an audit event persisted in the same transaction."""
    await _seed_subscriber(sqlite_conn, "alice@example.com", SubscriptionGranularity.GLOBAL, None)

    begin, submit, finalize, _ = _build_use_cases(
        uow_factory=uow_factory,
        clock=clock,
        template_repo=template_repo,
        template_renderer=template_renderer,
        tag_vocab=tag_vocab,
        mailer=mailer,
        scheduler=scheduler,
    )

    run_id = await begin.execute(
        BeginRunCommand(
            pipeline_type="etl-nightly",
            tags=frozenset({"production"}),
            declared_stages=(
                DeclaredStageInput(
                    stage_id="extract",
                    stage_order=0,
                    report_template_ref=TemplateRef(name="extract_report", version="1.0"),
                ),
            ),
            attachment_mode=AttachmentMode.PER_STAGE,
        )
    )
    await submit.execute(
        SubmitStageReportCommand(
            run_id=run_id,
            stage_id="extract",
            report_context={"metric": 1},
        )
    )
    await finalize.execute(FinalizeRunCommand(run_id=run_id))
    await scheduler.await_all(timeout=5.0)

    # Pull all audit rows. The SUBMIT_STAGE_REPORT event uses a
    # compound resource string ``run:<id>/stage:<sid>`` while the
    # others use ``run:<id>`` alone, so we query unfiltered and
    # filter on the Python side by whether the resource contains
    # the run_id.
    async with uow_factory() as uow:
        events = await uow.audit_log.query(limit=1000)

    run_events = [e for e in events if run_id in e.resource]
    actions = [e.action for e in run_events]
    # The exact audit-action taxonomy depends on each use case; we
    # assert the core ones are present.
    assert AuditAction.BEGIN_RUN in actions
    assert AuditAction.SUBMIT_STAGE_REPORT in actions
    assert AuditAction.FINALIZE_RUN in actions
    assert AuditAction.SEND_REPORT in actions


# -----------------------------------------------------------------------------
# Retry submission — stage state becomes RETRIED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_submission_updates_stage_to_retried(
    sqlite_conn: aiosqlite.Connection,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    template_repo: InMemoryTemplateRepository,
    template_renderer: Jinja2SandboxedTemplateRenderer,
    tag_vocab: InMemoryTagVocabulary,
    mailer: AsyncMock,
    scheduler: AsyncioBackgroundTaskScheduler,
) -> None:
    from message_service.domain.ids import StageId
    from message_service.domain.state_machines.stage_states import StageState

    begin, submit, _, _ = _build_use_cases(
        uow_factory=uow_factory,
        clock=clock,
        template_repo=template_repo,
        template_renderer=template_renderer,
        tag_vocab=tag_vocab,
        mailer=mailer,
        scheduler=scheduler,
    )
    run_id = await begin.execute(
        BeginRunCommand(
            pipeline_type="etl-nightly",
            tags=frozenset(),
            declared_stages=(
                DeclaredStageInput(
                    stage_id="extract",
                    stage_order=0,
                    report_template_ref=TemplateRef(name="extract_report", version="1.0"),
                ),
            ),
            attachment_mode=AttachmentMode.PER_STAGE,
        )
    )

    # First submission → SUBMITTED
    res1 = await submit.execute(
        SubmitStageReportCommand(run_id=run_id, stage_id="extract", report_context={"v": 1})
    )
    assert res1.was_retry is False
    assert res1.stage_state is StageState.SUBMITTED

    # Second submission → RETRIED, context overwritten
    res2 = await submit.execute(
        SubmitStageReportCommand(run_id=run_id, stage_id="extract", report_context={"v": 2})
    )
    assert res2.was_retry is True
    assert res2.stage_state is StageState.RETRIED

    async with uow_factory() as uow:
        stage = await uow.stage_repo.get(RunId(run_id), StageId("extract"))
        assert stage.state is StageState.RETRIED
        # Newer context wins (idempotent upsert).
        assert stage.report_context_json is not None
        assert '"v":2' in stage.report_context_json
