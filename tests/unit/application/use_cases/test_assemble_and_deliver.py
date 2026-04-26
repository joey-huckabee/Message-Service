"""Unit tests for :class:`AssembleAndDeliverUseCase` (background phase).

Covers the full workflow matrix: both attachment modes, happy path +
all expected-error branches + zero-recipient short-circuit.

Every test uses ``AsyncMock(spec=...)`` for ports so verification is
precise.

Requirement references
----------------------
L1-AGGR-002, L1-AGGR-003, L1-MAIL-001, L1-SUB-004
L2-AGGR-004, L2-AGGR-005, L2-AGGR-006, L2-AGGR-007, L2-AGGR-008
L2-MAIL-012
L3-AGGR-006, L3-AGGR-007, L3-AGGR-008, L3-AGGR-009, L3-AGGR-010, L3-AGGR-011
L3-RUN-026
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.clock import Clock
from message_service.application.ports.mailer import Mailer, OutboundEmail
from message_service.application.ports.report_store import ReportStore
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import (
    SubscriptionRepository,
)
from message_service.application.ports.template_renderer import TemplateRenderer
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.assemble_and_deliver import (
    AssembleAndDeliverUseCase,
    _build_attachment_filename,
    _sanitize_filename_component,
)
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    ContextSizeExceededError,
    EmailDeliveryError,
    PersistenceError,
    RenderedSizeExceededError,
    TemplateRenderError,
)
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 19, 12, 30, 0, tzinfo=UTC)
_RID = RunId("00000000-0000-4000-8000-000000000001")
_TPL_AGG = TemplateRef(name="nightly_summary", version="1.0")
_TPL_EXT = TemplateRef(name="extract_rpt", version="1.0")
_TPL_XFM = TemplateRef(name="transform_rpt", version="1.0")
_TPL_BODY = TemplateRef(name="default_body", version="1.0")
_FROM = "svc@example.com"


def _run(
    *,
    state: RunState = RunState.SENDING,
    attachment_mode: AttachmentMode = AttachmentMode.SINGLE_AGGREGATED,
    aggregation_template_ref: TemplateRef | None = _TPL_AGG,
) -> Run:
    return Run(
        run_id=_RID,
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(
                stage_id=StageId("extract"),
                stage_order=0,
                report_template_ref=_TPL_EXT,
            ),
            DeclaredStage(
                stage_id=StageId("transform"),
                stage_order=1,
                report_template_ref=_TPL_XFM,
            ),
        ),
        state=state,
        attachment_mode=attachment_mode,
        aggregation_template_ref=aggregation_template_ref,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=_T0,
        updated_at=_T0,
    )


def _stage(
    stage_id: str,
    *,
    state: StageState = StageState.SUBMITTED,
    report_template_ref: TemplateRef = _TPL_EXT,
    report_context_json: str | None = '{"metric": 42}',
) -> Stage:
    # PENDING stages must not carry a submitted_at timestamp; every
    # other state MUST.
    submitted_at = None if state is StageState.PENDING else _T0
    return Stage(
        run_id=_RID,
        stage_id=StageId(stage_id),
        state=state,
        report_template_ref=report_template_ref,
        report_context_json=report_context_json,
        email_body_context_json=None,
        submitted_at=submitted_at,
    )


@pytest.fixture
def clock() -> MagicMock:
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T1
    return clk


@pytest.fixture
def renderer() -> MagicMock:
    """Renderer that returns 'RENDERED:<ref-name>:<context-summary>'."""
    r = MagicMock(spec=TemplateRenderer)
    r.render.side_effect = lambda ref, ctx: f"<html>RENDERED:{ref.name}:{len(ctx)}</html>"
    return r


@pytest.fixture
def mailer() -> AsyncMock:
    return AsyncMock(spec=Mailer)


@pytest.fixture
def uow_factory() -> tuple[
    MagicMock,
    AsyncMock,  # uow
    AsyncMock,  # run_repo
    AsyncMock,  # stage_repo
    AsyncMock,  # subscription_repo
    AsyncMock,  # audit_log
]:
    """A factory that returns a fresh UoW each call, with state-evolving run_repo.

    The use case opens multiple UoWs per run; using the same repo mocks
    across all of them lets tests configure behavior once
    (``run_repo.set_initial(...``) and have every UoW see it.

    ``run_repo.get`` is wrapped so that subsequent calls reflect prior
    ``update_state`` calls — simulates the DB rolling forward through
    ``READY -> SENDING -> SENT/FAILED``. Without this, the use case's
    second UoW reads a stale ``READY`` state and the state machine
    rejects ``READY -> SENT``.
    """
    audit_log = AsyncMock(spec=AuditLog)
    run_repo = AsyncMock(spec=RunRepository)
    stage_repo = AsyncMock(spec=StageRepository)
    subscription_repo = AsyncMock(spec=SubscriptionRepository)

    # Track the "current" run state across update_state calls by
    # mutating what run_repo.get returns. We use a mutable closure
    # because dataclasses.replace on the Run preserves all other
    # fields.
    _current_run: list[Run | None] = [None]

    def _set_return(run: Run) -> None:
        _current_run[0] = run

    async def _get(run_id: RunId) -> Run:
        if _current_run[0] is None:
            raise AssertionError("Test forgot to set run_repo.get.return_value")
        return _current_run[0]

    async def _update_state(run_id: RunId, new_state: RunState, _now: Any) -> None:
        if _current_run[0] is None:
            return
        from dataclasses import replace

        _current_run[0] = replace(_current_run[0], state=new_state)

    # The custom get/update_state handlers replace the AsyncMock defaults.
    # We still expose the original AsyncMock so tests can inspect
    # call_args_list for assertions.
    run_repo.get.side_effect = _get
    run_repo.update_state.side_effect = _update_state

    # Allow tests to set the initial run via run_repo.set_initial(...
    # by hooking the attribute through a property-like bridge.
    def _configure_initial_run(run: Run) -> None:
        _set_return(run)

    # Attach the configurator as a plain attribute on the mock for
    # ergonomic test use: ``run_repo.set_initial(run)``.
    run_repo.set_initial = _configure_initial_run

    def _make_uow() -> AsyncMock:
        uow = AsyncMock(spec=UnitOfWork)
        uow.run_repo = run_repo
        uow.stage_repo = stage_repo
        uow.subscription_repo = subscription_repo
        uow.audit_log = audit_log
        uow.__aenter__.return_value = uow
        uow.__aexit__.return_value = None
        return uow

    single_uow = _make_uow()
    factory = MagicMock(side_effect=lambda: _make_uow())
    factory.return_value = single_uow
    return factory, single_uow, run_repo, stage_repo, subscription_repo, audit_log


@pytest.fixture
def use_case(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, Any, Any, Any, Any],
) -> AssembleAndDeliverUseCase:
    factory, _, _, _, _, _ = uow_factory
    return AssembleAndDeliverUseCase(
        uow_factory=factory,
        clock=clock,
        template_renderer=renderer,
        mailer=mailer,
        from_address=_FROM,
        email_body_template_ref=_TPL_BODY,
    )


# -----------------------------------------------------------------------------
# Filename helpers (unit tests for the pure functions)
# -----------------------------------------------------------------------------


@pytest.mark.requirement("L3-AGGR-010")
def test_sanitize_filename_replaces_disallowed_chars_with_underscore() -> None:
    assert _sanitize_filename_component("etl nightly/v2") == "etl_nightly_v2"
    assert _sanitize_filename_component("a.b-c_d") == "a.b-c_d"  # safe chars preserved


@pytest.mark.requirement("L2-AGGR-006")
def test_build_attachment_filename_single_aggregated_mode() -> None:
    fn = _build_attachment_filename("etl-nightly", _RID)
    assert fn == f"etl-nightly_{_RID}.html"


@pytest.mark.requirement("L2-AGGR-006")
def test_build_attachment_filename_per_stage_mode() -> None:
    fn = _build_attachment_filename("etl-nightly", _RID, stage_id="extract")
    assert fn == f"etl-nightly_{_RID}_extract.html"


@pytest.mark.requirement("L3-AGGR-011")
def test_build_attachment_filename_caps_at_255_bytes() -> None:
    long_stage_id = "x" * 300
    fn = _build_attachment_filename("etl", _RID, stage_id=long_stage_id)
    assert len(fn.encode("utf-8")) == 255
    assert fn.endswith(".html")


@pytest.mark.requirement("L3-AGGR-010")
def test_build_attachment_filename_sanitizes_both_components() -> None:
    fn = _build_attachment_filename("etl nightly!", _RID, stage_id="stage/1")
    assert "etl_nightly_" in fn
    assert "stage_1" in fn
    assert "!" not in fn
    assert "/" not in fn


# -----------------------------------------------------------------------------
# Happy path — SINGLE_AGGREGATED mode with subscribers
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AGGR-004")
async def test_happy_path_single_aggregated_sends_one_attachment(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(state=RunState.READY, attachment_mode=AttachmentMode.SINGLE_AGGREGATED)
    )
    stage_repo.list_by_run.return_value = [
        _stage("extract"),
        _stage("transform", report_template_ref=_TPL_XFM),
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})

    await use_case.execute(_RID)

    mailer.send.assert_awaited_once()
    email: OutboundEmail = mailer.send.call_args.args[0]
    assert len(email.attachments) == 1
    assert email.attachments[0].filename == f"etl-nightly_{_RID}.html"


@pytest.mark.asyncio
@pytest.mark.requirement("L1-AGGR-002")
async def test_happy_path_outbound_email_has_correct_shape(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset(
        {"alice@example.com", "bob@example.com"}
    )

    await use_case.execute(_RID)

    email: OutboundEmail = mailer.send.call_args.args[0]
    assert email.from_address == _FROM
    assert _RID in email.subject
    assert "etl-nightly" in email.subject
    assert email.recipients == frozenset({"alice@example.com", "bob@example.com"})
    assert email.body_html.startswith("<html>")


# -----------------------------------------------------------------------------
# PER_STAGE mode
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AGGR-005")
async def test_per_stage_produces_one_attachment_per_non_empty_stage(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(
            state=RunState.READY,
            attachment_mode=AttachmentMode.PER_STAGE,
            aggregation_template_ref=None,
        )
    )
    stage_repo.list_by_run.return_value = [
        _stage("extract"),
        _stage("transform", report_template_ref=_TPL_XFM),
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})

    await use_case.execute(_RID)

    email: OutboundEmail = mailer.send.call_args.args[0]
    assert len(email.attachments) == 2
    filenames = {a.filename for a in email.attachments}
    assert f"etl-nightly_{_RID}_extract.html" in filenames
    assert f"etl-nightly_{_RID}_transform.html" in filenames


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-009")
async def test_per_stage_with_all_empty_produces_zero_attachments(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
    renderer: MagicMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(
            state=RunState.READY,
            attachment_mode=AttachmentMode.PER_STAGE,
            aggregation_template_ref=None,
        )
    )
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})
    # Renderer returns empty for every ref EXCEPT the email body template.
    renderer.render.side_effect = lambda ref, ctx: "" if ref != _TPL_BODY else "<html>body</html>"

    await use_case.execute(_RID)

    email: OutboundEmail = mailer.send.call_args.args[0]
    assert email.attachments == ()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-008")
async def test_per_stage_whitespace_only_excluded_as_empty(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
    renderer: MagicMock,
) -> None:
    """A stage whose rendered fragment is only whitespace is empty."""
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(
            state=RunState.READY,
            attachment_mode=AttachmentMode.PER_STAGE,
            aggregation_template_ref=None,
        )
    )
    stage_repo.list_by_run.return_value = [
        _stage("extract"),
        _stage("transform", report_template_ref=_TPL_XFM),
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})

    # extract renders to whitespace; transform renders normally.
    def _render(ref: TemplateRef, ctx: dict[str, Any]) -> str:
        if ref == _TPL_EXT:
            return "   \n\n  "  # whitespace-only
        if ref == _TPL_BODY:
            return "<html>body</html>"
        return "<html>transform content</html>"

    renderer.render.side_effect = _render

    await use_case.execute(_RID)

    email: OutboundEmail = mailer.send.call_args.args[0]
    assert len(email.attachments) == 1
    assert "transform" in email.attachments[0].filename


# -----------------------------------------------------------------------------
# Stage filtering: PENDING/TIMEOUT/FAILED stages excluded from assembly
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-008")
async def test_pending_stages_excluded_from_assembly(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(
            state=RunState.READY,
            attachment_mode=AttachmentMode.PER_STAGE,
            aggregation_template_ref=None,
        )
    )
    stage_repo.list_by_run.return_value = [
        _stage("extract", state=StageState.SUBMITTED),
        _stage(
            "transform",
            state=StageState.PENDING,
            report_template_ref=_TPL_XFM,
            report_context_json=None,
        ),
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})

    await use_case.execute(_RID)

    email: OutboundEmail = mailer.send.call_args.args[0]
    assert len(email.attachments) == 1
    assert "extract" in email.attachments[0].filename


# -----------------------------------------------------------------------------
# Stage ordering (L2-AGGR-007, L2-AGGR-008)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-AGGR-007")
async def test_stages_rendered_in_stage_order_not_submission_order(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
    renderer: MagicMock,
) -> None:
    """Rendered stage fragments SHALL be in stage_order, regardless of DB order."""
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(
        _run(
            state=RunState.READY,
            attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
        )
    )
    # Return in "wrong" order to force the use case to sort.
    stage_repo.list_by_run.return_value = [
        _stage("transform", report_template_ref=_TPL_XFM),  # stage_order=1
        _stage("extract"),  # stage_order=0
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    await use_case.execute(_RID)

    # Inspect the aggregation template render call (the one with
    # ref=_TPL_AGG). The `stages` in its context SHALL be ordered
    # extract, transform.
    agg_call = next(c for c in renderer.render.call_args_list if c.args[0] == _TPL_AGG)
    ctx = agg_call.args[1]
    stage_ids = [s["stage_id"] for s in ctx["stages"]]
    assert stage_ids == ["extract", "transform"]


# -----------------------------------------------------------------------------
# Zero recipients (short-circuit to SENT, no mailer call)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
@pytest.mark.requirement("L3-OBS-037")
async def test_zero_recipients_finalizes_sent_without_mailer_call(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset()

    await use_case.execute(_RID)

    mailer.send.assert_not_called()
    # SEND_REPORT audit still recorded with recipient_count=0.
    send_report_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].action == AuditAction.SEND_REPORT
    ]
    assert len(send_report_events) == 1
    event = send_report_events[0]
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.details["recipient_count"] == 0
    assert event.details["recipient_addresses"] == []


@pytest.mark.asyncio
@pytest.mark.requirement("L1-SUB-004")
async def test_zero_recipients_still_transitions_to_sent(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset()

    await use_case.execute(_RID)

    # The final transition is to SENT.
    transitions = [c.args[1] for c in run_repo.update_state.call_args_list]
    assert RunState.SENT in transitions


# -----------------------------------------------------------------------------
# Error handling: TemplateRenderError -> FAILED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-007")
async def test_template_render_error_transitions_to_failed(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
    mailer: AsyncMock,
    renderer: MagicMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    renderer.render.side_effect = TemplateRenderError(
        "syntax error in template", details={"template": "extract_rpt"}
    )

    await use_case.execute(_RID)

    # Final run state is FAILED.
    transitions = [c.args[1] for c in run_repo.update_state.call_args_list]
    assert RunState.FAILED in transitions
    # Mailer never called.
    mailer.send.assert_not_called()
    # Subscription lookup never called (fails before recipient resolution).
    subscription_repo.list_recipients_for_run.assert_not_called()
    # FAILURE audit event carries the reason.
    failure_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].outcome == AuditOutcome.FAILURE
    ]
    assert len(failure_events) == 1
    assert failure_events[0].details["failure_reason"] == "TEMPLATE_RENDER"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-AGGR-007")
async def test_rendered_size_exceeded_transitions_to_failed(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any, AsyncMock],
    mailer: AsyncMock,
    renderer: MagicMock,
) -> None:
    _, _, run_repo, stage_repo, _, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    renderer.render.side_effect = RenderedSizeExceededError(
        "too big",
        details={"measured_bytes": 20_000_000, "limit_bytes": 10_000_000},
    )

    await use_case.execute(_RID)

    mailer.send.assert_not_called()
    failure_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].outcome == AuditOutcome.FAILURE
    ]
    assert len(failure_events) == 1
    assert failure_events[0].details["failure_reason"] == "RENDERED_SIZE_EXCEEDED"


@pytest.mark.asyncio
async def test_context_size_exceeded_transitions_to_failed(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any, AsyncMock],
    mailer: AsyncMock,
    renderer: MagicMock,
) -> None:
    _, _, run_repo, stage_repo, _, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    renderer.render.side_effect = ContextSizeExceededError(
        "context too big", details={"measured_bytes": 5_000_000}
    )

    await use_case.execute(_RID)

    mailer.send.assert_not_called()
    failure_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].outcome == AuditOutcome.FAILURE
    ]
    assert len(failure_events) == 1
    assert failure_events[0].details["failure_reason"] == "CONTEXT_SIZE_EXCEEDED"


# -----------------------------------------------------------------------------
# Error handling: EmailDeliveryError -> FAILED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-MAIL-001")
@pytest.mark.requirement("L3-OBS-037")
async def test_email_delivery_error_transitions_to_failed(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
    mailer: AsyncMock,
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"alice@example.com"})
    mailer.send.side_effect = EmailDeliveryError(
        "SMTP 550",
        details={"smtp_code": 550},
    )

    await use_case.execute(_RID)

    mailer.send.assert_awaited_once()
    transitions = [c.args[1] for c in run_repo.update_state.call_args_list]
    assert RunState.FAILED in transitions
    failure_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].outcome == AuditOutcome.FAILURE
    ]
    assert len(failure_events) == 1
    assert failure_events[0].details["failure_reason"] == "EMAIL_DELIVERY"
    assert failure_events[0].details["recipient_count"] == 1


# -----------------------------------------------------------------------------
# Unexpected errors propagate (don't silently fail)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_error_propagates_to_scheduler(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, Any, Any, Any],
) -> None:
    """AttributeError (programming bug) SHALL NOT be silently swallowed."""
    _, _, run_repo, _, _, _ = uow_factory
    run_repo.get.side_effect = AttributeError("bug: something is None")

    with pytest.raises(AttributeError, match="bug"):
        await use_case.execute(_RID)


# -----------------------------------------------------------------------------
# State transitions: READY -> SENDING, then SENDING -> SENT/FAILED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-004")
async def test_happy_path_transitions_through_ready_sending_sent(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    await use_case.execute(_RID)

    transitions = [c.args[1] for c in run_repo.update_state.call_args_list]
    assert transitions == [RunState.SENDING, RunState.SENT]


# -----------------------------------------------------------------------------
# Audit: delivery audit fields per L2-MAIL-012
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-MAIL-012")
@pytest.mark.requirement("L3-OBS-037")
async def test_delivery_success_audit_carries_required_fields(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset(
        {"alice@example.com", "bob@example.com"}
    )

    await use_case.execute(_RID)

    send_events = [
        c.args[0]
        for c in audit_log.record.call_args_list
        if c.args[0].action == AuditAction.SEND_REPORT
    ]
    assert len(send_events) == 1
    details = send_events[0].details
    # L2-MAIL-012 required fields:
    assert details["run_id"] == _RID
    assert details["recipient_count"] == 2
    assert set(details["recipient_addresses"]) == {
        "alice@example.com",
        "bob@example.com",
    }


# -----------------------------------------------------------------------------
# Audit-first ordering (L3-RUN-026)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
async def test_audit_precedes_state_update_on_ready_to_sending(
    use_case: AssembleAndDeliverUseCase,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    _, _, run_repo, stage_repo, subscription_repo, audit_log = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    manager = MagicMock()
    manager.attach_mock(audit_log.record, "audit")
    manager.attach_mock(run_repo.update_state, "update_state")

    await use_case.execute(_RID)

    call_names = [c[0] for c in manager.mock_calls]
    # Every update_state is preceded by at least one audit call.
    for i, name in enumerate(call_names):
        if name == "update_state":
            assert "audit" in call_names[:i], (
                f"update_state at index {i} not preceded by audit: {call_names}"
            )


# -----------------------------------------------------------------------------
# ReportStore wiring (Increment 19c)
# -----------------------------------------------------------------------------


def _build_use_case_with_store(
    *,
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    factory: MagicMock,
    report_store: MagicMock,
) -> AssembleAndDeliverUseCase:
    return AssembleAndDeliverUseCase(
        uow_factory=factory,
        clock=clock,
        template_renderer=renderer,
        mailer=mailer,
        from_address=_FROM,
        email_body_template_ref=_TPL_BODY,
        report_store=report_store,
    )


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_sent_path_saves_each_rendered_fragment(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """L3-PERS-024: every non-empty rendered fragment SHALL be persisted."""
    factory, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [
        _stage("extract"),
        _stage("transform", report_template_ref=_TPL_XFM),
    ]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    store = MagicMock(spec=ReportStore)
    use_case = _build_use_case_with_store(
        clock=clock,
        renderer=renderer,
        mailer=mailer,
        factory=factory,
        report_store=store,
    )
    await use_case.execute(_RID)

    saved_stages = sorted(call.args[1] for call in store.save_fragment.call_args_list)
    assert saved_stages == ["extract", "transform"]


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_sent_path_saves_assembled_email_body(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """L3-PERS-024: the assembled email body SHALL be saved on SENT."""
    factory, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    store = MagicMock(spec=ReportStore)
    use_case = _build_use_case_with_store(
        clock=clock,
        renderer=renderer,
        mailer=mailer,
        factory=factory,
        report_store=store,
    )
    await use_case.execute(_RID)

    store.save_email_body.assert_called_once()
    saved_run_id, saved_html = store.save_email_body.call_args.args
    assert saved_run_id == _RID
    assert saved_html.startswith("<html>")


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_zero_recipient_path_still_saves_email_body(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """The body SHALL be saved even when recipients is empty (run still SENT)."""
    factory, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset()

    store = MagicMock(spec=ReportStore)
    use_case = _build_use_case_with_store(
        clock=clock,
        renderer=renderer,
        mailer=mailer,
        factory=factory,
        report_store=store,
    )
    await use_case.execute(_RID)

    mailer.send.assert_not_awaited()
    store.save_email_body.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_failed_delivery_does_not_save_email_body(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """If Mailer.send fails, the assembled body SHALL NOT be saved.

    The viewer route's 404-on-missing semantics is exactly the
    intended behavior for failed runs (L3-DASH-029).
    """
    factory, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})
    mailer.send.side_effect = EmailDeliveryError("smtp 5xx", details={"retriable": False})

    store = MagicMock(spec=ReportStore)
    use_case = _build_use_case_with_store(
        clock=clock,
        renderer=renderer,
        mailer=mailer,
        factory=factory,
        report_store=store,
    )
    await use_case.execute(_RID)

    store.save_email_body.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-PERS-024")
async def test_persistence_error_during_save_is_swallowed(
    clock: MagicMock,
    renderer: MagicMock,
    mailer: AsyncMock,
    uow_factory: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    """Save errors SHALL NOT abort delivery — the email is the source of truth."""
    factory, _, run_repo, stage_repo, subscription_repo, _ = uow_factory
    run_repo.set_initial(_run(state=RunState.READY))
    stage_repo.list_by_run.return_value = [_stage("extract")]
    subscription_repo.list_recipients_for_run.return_value = frozenset({"a@x"})

    store = MagicMock(spec=ReportStore)
    store.save_fragment.side_effect = PersistenceError("disk full", details={"path": "/tmp/x"})
    store.save_email_body.side_effect = PersistenceError("disk full", details={"path": "/tmp/y"})
    use_case = _build_use_case_with_store(
        clock=clock,
        renderer=renderer,
        mailer=mailer,
        factory=factory,
        report_store=store,
    )

    # Should NOT raise — saves are best-effort.
    await use_case.execute(_RID)

    mailer.send.assert_awaited_once()
