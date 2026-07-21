"""Application-layer tests for :class:`ResendRunUseCase` (Increment 19b).

Drives a real SQLite UoW so the audit + recipient-resolution paths
are exercised against real persistence. The render path
(:meth:`AssembleAndDeliverUseCase.prepare_email`) is stubbed because
its full integration is covered by ``test_assemble_and_deliver`` and
the end-to-end pipeline test; here we focus on the resend-specific
contract: state precondition, audit format, current-recipient
resolution, and mailer-failure handling.

Carries ``@pytest.mark.allow_io`` because the tests legitimately
drive a real :class:`SqliteUnitOfWorkFactory` to verify atomic
audit + repo behavior (same pattern as the other application-layer
use-case tests under ``tests/unit/application/use_cases/``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from message_service.application.ports.clock import Clock
from message_service.application.ports.mailer import EmailAttachment, OutboundEmail
from message_service.application.use_cases.assemble_and_deliver import (
    PreparedEmail,
    _build_subject,
)
from message_service.application.use_cases.resend_run import ResendRunUseCase
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.subscription import SubscriptionGranularity
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.aggregates.user import User
from message_service.domain.errors import (
    EmailDeliveryError,
    InvalidRunStateError,
    TemplateRenderError,
)
from message_service.domain.ids import RunId, StageId, UserId
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

pytestmark = pytest.mark.allow_io


_T0 = datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC)


class _FixedClock(Clock):
    def __init__(self, value: datetime = _T0) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


class _RecordingMailer:
    """Mailer stub that records sends and can be configured to fail."""

    def __init__(self, *, raise_on_send: EmailDeliveryError | None = None) -> None:
        self._raise = raise_on_send
        self.sent: list[OutboundEmail] = []

    async def send(self, message: OutboundEmail) -> None:
        if self._raise is not None:
            raise self._raise
        self.sent.append(message)


class _StubAssemble:
    """Stand-in for AssembleAndDeliverUseCase exposing prepare_email + build_subject.

    ``build_subject`` mirrors the real chokepoint (default format + per-pipeline
    ``subject_templates`` override) so resend tests can assert the resend path
    delegates to it (L3-MAIL-034). The real method's behavior is verified in
    ``test_assemble_and_deliver``.
    """

    def __init__(
        self,
        *,
        body_html: str = "<p>x</p>",
        subject_templates: dict[str, str] | None = None,
        raise_on_prepare: BaseException | None = None,
    ) -> None:
        self._body_html = body_html
        self.prepare_calls: list[RunId] = []
        self._subject_templates = dict(subject_templates or {})
        self.build_subject_calls: list[Run] = []
        self._raise_on_prepare = raise_on_prepare

    def build_subject(self, run: Run) -> str:
        self.build_subject_calls.append(run)
        return _build_subject(
            run.pipeline_type, run.run_id, self._subject_templates.get(run.pipeline_type)
        )

    async def prepare_email(self, run_id: RunId) -> PreparedEmail:
        self.prepare_calls.append(run_id)
        if self._raise_on_prepare is not None:
            raise self._raise_on_prepare
        # Need a Run to populate PreparedEmail; load via the test's
        # uow_factory via a closure -- but the tests construct a
        # PreparedEmail with an explicit run. Simpler: the test fixture
        # passes the run in via a setter.
        assert self._run is not None, "stub run not configured"
        return PreparedEmail(
            run=self._run,
            body_html=self._body_html,
            attachments=(
                EmailAttachment(
                    filename="report.html",
                    content_type="text/html",
                    content=b"<p>x</p>",
                ),
            ),
        )

    def configure_run(self, run: Run) -> None:
        self._run = run


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------


@pytest.fixture
async def sqlite_conn() -> AsyncIterator[aiosqlite.Connection]:
    c = await open_connection(Path(":memory:"))
    try:
        await apply_migrations(c)
        yield c
    finally:
        await c.close()


@pytest.fixture
def clock() -> _FixedClock:
    return _FixedClock()


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


def _make_run(*, run_id: str, state: RunState) -> Run:
    return Run(
        run_id=RunId(run_id),
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=TemplateRef(name="r", version="1.0"),
            ),
        ),
        state=state,
        attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
        aggregation_template_ref=TemplateRef(name="agg", version="1.0"),
        subscription_predicate_tags=frozenset({"production"}),
        created_at=_T0,
        updated_at=_T0,
    )


async def _seed_run(uow_factory: SqliteUnitOfWorkFactory, run: Run) -> None:
    async with uow_factory() as uow:
        await uow.run_repo.save(run)
        await uow.commit()


async def _seed_user_and_subscription(
    uow_factory: SqliteUnitOfWorkFactory,
    *,
    email: str = "alice@example.com",
    granularity: SubscriptionGranularity = SubscriptionGranularity.GLOBAL,
    target_value: str | None = None,
) -> User:
    async with uow_factory() as uow:
        saved = await uow.user_repo.save(
            User(
                email=email,
                display_name=email.split("@", 1)[0],
                password_hash="$argon2id$v=19$x$y$z",
                created_at=_T0,
                disabled=False,
                is_admin=False,
            ),
        )
        await uow.subscription_repo.add(
            user_id=UserId(saved.user_id) if saved.user_id else UserId(0),
            granularity=granularity,
            target_value=target_value,
        )
        await uow.commit()
    return saved


def _build_use_case(
    *,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
    mailer: Any,
    assemble: _StubAssemble,
) -> ResendRunUseCase:
    return ResendRunUseCase(
        uow_factory=uow_factory,
        clock=clock,
        mailer=mailer,
        assemble_and_deliver=assemble,  # type: ignore[arg-type]
        from_address="svc@example.com",
    )


# -----------------------------------------------------------------------------
# State precondition (L3-DASH-028)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-028")
@pytest.mark.parametrize("state", [RunState.SENT, RunState.FAILED])
async def test_resend_permitted_for_sent_or_failed(
    state: RunState,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L3-DASH-028: SENT and FAILED runs SHALL be resendable."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000aa1", state=state)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    await use_case.execute(run_id=run.run_id, admin_user_id=42)

    assert len(mailer.sent) == 1
    assert assemble.prepare_calls == [run.run_id]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-028")
@pytest.mark.parametrize(
    "state",
    [
        RunState.INITIATED,
        RunState.AGGREGATING,
        RunState.READY,
        RunState.SENDING,
        RunState.ORPHANED,
    ],
)
async def test_resend_rejects_non_resendable_states(
    state: RunState,
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L3-DASH-028: any state other than SENT/FAILED SHALL raise."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000aa2", state=state)
    await _seed_run(uow_factory, run)

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    with pytest.raises(InvalidRunStateError) as exc_info:
        await use_case.execute(run_id=run.run_id, admin_user_id=42)
    assert exc_info.value.details["current_state"] == state.value
    assert mailer.sent == []
    assert assemble.prepare_calls == []


# -----------------------------------------------------------------------------
# Audit format (L3-DASH-013)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-013")
async def test_resend_emits_audit_with_required_fields(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """L3-DASH-013: audit SHALL carry action=RESEND_REPORT + required details."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000bb1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )
    await use_case.execute(run_id=run.run_id, admin_user_id=42)

    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.RESEND_REPORT))
    assert len(events) == 1
    audit = events[0]
    assert audit.action is AuditAction.RESEND_REPORT
    assert audit.outcome is AuditOutcome.SUCCESS
    assert audit.actor == "user:42"
    assert audit.resource == f"run:{run.run_id}"
    assert audit.details["run_id"] == run.run_id
    assert audit.details["recipient_count"] == 1
    assert audit.details["recipient_addresses"] == ["alice@example.com"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-013")
async def test_resend_render_failure_records_failure_audit_and_does_not_raise(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """A re-render failure SHALL be caught, audited FAILURE, and not propagate.

    Regression: prepare_email's render errors escaped uncaught — no
    RESEND_REPORT row, and the resend route (which handles only
    RunNotFoundError/InvalidRunStateError) surfaced a 500.
    """
    run = _make_run(run_id="00000000-0000-4000-8000-000000000cc1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble(
        raise_on_prepare=TemplateRenderError("template gone", details={"template": "agg"})
    )
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    # Must not raise.
    await use_case.execute(run_id=run.run_id, admin_user_id=7)

    # Nothing was delivered.
    assert mailer.sent == []

    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.RESEND_REPORT))
    assert len(events) == 1
    audit = events[0]
    assert audit.outcome is AuditOutcome.FAILURE
    assert audit.actor == "user:7"
    assert audit.details["failure_reason"] == "TemplateRenderError"
    assert audit.details["recipient_count"] == 0
    assert audit.details["attachment_count"] == 0


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-013")
async def test_resend_schema_violation_records_failure_audit_and_does_not_raise(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """A ContextSchemaViolationError on resend SHALL be caught + audited, not 500.

    Regression: this render error was not in the caught set, so a resend of a
    schema-violating run escaped to the route as an unhandled 500.
    """
    from message_service.domain.errors import ContextSchemaViolationError

    run = _make_run(run_id="00000000-0000-4000-8000-000000000cc2", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble(
        raise_on_prepare=ContextSchemaViolationError(
            "context failed schema", details={"json_pointer": "/count", "instance_value": "x"}
        )
    )
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    await use_case.execute(run_id=run.run_id, admin_user_id=7)  # must not raise

    assert mailer.sent == []
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.RESEND_REPORT))
    assert len(events) == 1
    assert events[0].outcome is AuditOutcome.FAILURE
    assert events[0].details["failure_reason"] == "ContextSchemaViolationError"


# -----------------------------------------------------------------------------
# Recipient resolution at resend time (L3-DASH-012)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-DASH-012")
@pytest.mark.requirement("L3-DASH-027")
async def test_resend_uses_current_subscriber_set_not_original(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """L3-DASH-012: a subscription added after the original send SHALL receive the resent email."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000cc1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    # Original subscriber.
    await _seed_user_and_subscription(uow_factory, email="alice@example.com")
    # New subscriber added "after" the original send (just before resend).
    await _seed_user_and_subscription(uow_factory, email="bob@example.com")

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )
    await use_case.execute(run_id=run.run_id, admin_user_id=42)

    assert len(mailer.sent) == 1
    assert mailer.sent[0].recipients == frozenset({"alice@example.com", "bob@example.com"})


# -----------------------------------------------------------------------------
# Zero-recipient + delivery-failure paths
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resend_zero_recipients_audits_success_without_mailer_call(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """No subscribers SHALL audit SUCCESS with recipient_count=0; mailer not invoked."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000dd1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    # NOTE: no subscriptions seeded.

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )
    await use_case.execute(run_id=run.run_id, admin_user_id=42)

    assert mailer.sent == []
    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.RESEND_REPORT))
    assert len(events) == 1
    assert events[0].outcome is AuditOutcome.SUCCESS
    assert events[0].details["recipient_count"] == 0


@pytest.mark.asyncio
async def test_resend_mailer_failure_audits_failure_and_returns(
    uow_factory: SqliteUnitOfWorkFactory, clock: _FixedClock
) -> None:
    """An EmailDeliveryError SHALL audit FAILURE without re-raising."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000ee1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble()
    assemble.configure_run(run)
    mailer = _RecordingMailer(
        raise_on_send=EmailDeliveryError(
            "smtp 451",
            details={"retriable": True, "smtp_code": 451},
        ),
    )
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )
    # Must NOT raise.
    await use_case.execute(run_id=run.run_id, admin_user_id=42)

    async with uow_factory() as uow:
        events = list(await uow.audit_log.query(action=AuditAction.RESEND_REPORT))
    assert len(events) == 1
    assert events[0].outcome is AuditOutcome.FAILURE
    assert events[0].details["recipient_count"] == 1
    assert "failure_reason" in events[0].details


# -----------------------------------------------------------------------------
# Resend subject conformance with L2-MAIL-014 (L3-MAIL-034)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-034")
async def test_resend_subject_honors_pipeline_override(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L3-MAIL-034: a resend honors the per-pipeline subject_templates override."""
    run = _make_run(run_id="00000000-0000-4000-8000-000000000ac1", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble(
        subject_templates={"etl-nightly": "[NIGHTLY:{pipeline_type}] run {run_id}"}
    )
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    await use_case.execute(run_id=run.run_id, admin_user_id=7)

    assert len(mailer.sent) == 1
    assert mailer.sent[0].subject == f"[NIGHTLY:etl-nightly] run {run.run_id}"
    assert assemble.build_subject_calls == [run]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-MAIL-034")
async def test_resend_subject_uses_canonical_default_not_old_format(
    uow_factory: SqliteUnitOfWorkFactory,
    clock: _FixedClock,
) -> None:
    """L3-MAIL-034: an unconfigured pipeline resends with the L3-MAIL-027 default.

    Regression guard against the old resend-only ``Run {run_id} -- {pipeline_type}``
    subject.
    """
    run = _make_run(run_id="00000000-0000-4000-8000-000000000ac2", state=RunState.SENT)
    await _seed_run(uow_factory, run)
    await _seed_user_and_subscription(uow_factory)

    assemble = _StubAssemble()  # no per-pipeline overrides
    assemble.configure_run(run)
    mailer = _RecordingMailer()
    use_case = _build_use_case(
        uow_factory=uow_factory, clock=clock, mailer=mailer, assemble=assemble
    )

    await use_case.execute(run_id=run.run_id, admin_user_id=7)

    assert mailer.sent[0].subject == f"[etl-nightly] run {run.run_id}"
    assert not mailer.sent[0].subject.startswith("Run ")
