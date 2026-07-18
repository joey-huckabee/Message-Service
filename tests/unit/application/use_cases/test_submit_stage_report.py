"""Unit tests for :class:`SubmitStageReportUseCase`.

Every test uses :class:`unittest.mock.AsyncMock` with ``spec=Port`` to
verify precise port interactions.

Requirement references
----------------------
L1-STAGE-002 (idempotent), L1-STAGE-003 (empty still moves out of PENDING),
L1-STAGE-004 (reject unknown stage)
L2-STAGE-004 (retry → RETRIED), L2-STAGE-005 (independent clearing),
L2-STAGE-008 (UNKNOWN_STAGE), L2-STAGE-009 (RUN_NOT_FOUND precedence)
L3-STAGE-006, L3-STAGE-010, L3-STAGE-011
L3-RUN-026 (audit-first ordering)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.clock import Clock
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.submit_stage_report import (
    SubmitStageReportUseCase,
)
from message_service.application.use_cases.submit_stage_report_command import (
    SubmitStageReportCommand,
)
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    InvalidRunStateError,
    InvalidStageStateError,
    MalformedRequestError,
    RunNotFoundError,
    UnknownStageError,
)
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState
from message_service.domain.state_machines.stage_states import StageState

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 19, 12, 5, 0, tzinfo=UTC)
_RID = RunId("00000000-0000-4000-8000-000000000001")
_SID_EXTRACT = StageId("extract")
_SID_TRANSFORM = StageId("transform")
_SID_UNKNOWN = StageId("not-declared")
_TPL_AGG = TemplateRef(name="nightly_summary", version="1.0")
_TPL_EXT = TemplateRef(name="extract_rpt", version="1.0")
_TPL_XFM = TemplateRef(name="transform_rpt", version="1.0")


def _sample_run(state: RunState = RunState.INITIATED) -> Run:
    return Run(
        run_id=_RID,
        pipeline_type="etl-nightly",
        tags=frozenset({"production"}),
        declared_stages=(
            DeclaredStage(stage_id=_SID_EXTRACT, stage_order=0, report_template_ref=_TPL_EXT),
            DeclaredStage(stage_id=_SID_TRANSFORM, stage_order=1, report_template_ref=_TPL_XFM),
        ),
        state=state,
        attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
        aggregation_template_ref=_TPL_AGG,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=_T0,
        updated_at=_T0,
    )


def _sample_stage(
    state: StageState = StageState.PENDING,
    submitted_at: datetime | None = None,
    report_context_json: str | None = None,
    email_body_context_json: str | None = None,
    email_body_position: EmailBodyPosition | None = None,
) -> Stage:
    # L3-AGGR-018: position is set iff an email body contribution is present.
    if email_body_position is None and email_body_context_json is not None:
        email_body_position = EmailBodyPosition.AFTER_STAGES_SUMMARY
    return Stage(
        run_id=_RID,
        stage_id=_SID_EXTRACT,
        state=state,
        report_template_ref=_TPL_EXT,
        report_context_json=report_context_json,
        email_body_context_json=email_body_context_json,
        email_body_position=email_body_position,
        submitted_at=submitted_at,
    )


@pytest.fixture
def clock() -> MagicMock:
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T1
    return clk


@pytest.fixture
def uow_bundle() -> tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Return ``(factory, uow, run_repo, stage_repo, audit_log)``."""
    audit_log = AsyncMock(spec=AuditLog)
    run_repo = AsyncMock(spec=RunRepository)
    stage_repo = AsyncMock(spec=StageRepository)

    uow = AsyncMock(spec=UnitOfWork)
    uow.run_repo = run_repo
    uow.stage_repo = stage_repo
    uow.audit_log = audit_log
    uow.__aenter__.return_value = uow
    uow.__aexit__.return_value = None

    factory = MagicMock(return_value=uow)
    return factory, uow, run_repo, stage_repo, audit_log


@pytest.fixture
def use_case(
    clock: MagicMock,
    uow_bundle: tuple[MagicMock, Any, Any, Any, Any],
) -> SubmitStageReportUseCase:
    factory, _, _, _, _ = uow_bundle
    return SubmitStageReportUseCase(uow_factory=factory, clock=clock)


def _valid_cmd(**overrides: Any) -> SubmitStageReportCommand:
    fields: dict[str, Any] = {
        "run_id": _RID,
        "stage_id": _SID_EXTRACT,
        "report_context": {"metric": 42},
        "email_body_context": {"summary": "ok"},
    }
    fields.update(overrides)
    # Mirror the command's L3-AGGR-018 pairing: position set iff body
    # context set. Callers may override email_body_position explicitly.
    if "email_body_position" not in fields:
        fields["email_body_position"] = (
            EmailBodyPosition.AFTER_STAGES_SUMMARY
            if fields.get("email_body_context") is not None
            else None
        )
    return SubmitStageReportCommand(**fields)


# -----------------------------------------------------------------------------
# Happy path — first submission (PENDING → SUBMITTED)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-006")
async def test_first_submission_transitions_pending_to_submitted(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.INITIATED)
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    result = await use_case.execute(_valid_cmd())

    assert result.stage_state == StageState.SUBMITTED
    assert result.was_retry is False


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-005")
async def test_first_submission_transitions_run_initiated_to_aggregating(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.INITIATED)
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd())

    run_repo.update_state.assert_awaited_once()
    args = run_repo.update_state.call_args
    assert args.args[0] == _RID
    assert args.args[1] == RunState.AGGREGATING


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-005")
async def test_run_already_aggregating_not_transitioned(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    """If the run is already AGGREGATING, do NOT call update_state again."""
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd())

    run_repo.update_state.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-006")
async def test_stage_saved_with_submitted_state_and_content(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run()
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd())

    stage_repo.save.assert_awaited_once()
    saved: Stage = stage_repo.save.call_args.args[0]
    assert saved.state == StageState.SUBMITTED
    assert saved.submitted_at == _T1
    assert saved.report_context_json is not None
    assert json.loads(saved.report_context_json) == {"metric": 42}
    assert saved.email_body_context_json is not None
    assert json.loads(saved.email_body_context_json) == {"summary": "ok"}


# -----------------------------------------------------------------------------
# Retry path — SUBMITTED → RETRIED, RETRIED → RETRIED
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-004")
async def test_second_submission_transitions_submitted_to_retried(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(
        state=StageState.SUBMITTED,
        submitted_at=_T0,
        report_context_json='{"previous":"data"}',
    )

    result = await use_case.execute(_valid_cmd())

    assert result.stage_state == StageState.RETRIED
    assert result.was_retry is True


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-004")
async def test_third_submission_stays_in_retried(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(state=StageState.RETRIED, submitted_at=_T0)

    result = await use_case.execute(_valid_cmd())

    assert result.stage_state == StageState.RETRIED
    assert result.was_retry is True


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-007")
async def test_retry_overwrites_prior_content(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(
        state=StageState.SUBMITTED,
        submitted_at=_T0,
        report_context_json='{"old":"content"}',
    )

    await use_case.execute(_valid_cmd(report_context={"new": "content"}))

    saved: Stage = stage_repo.save.call_args.args[0]
    assert saved.report_context_json is not None
    assert json.loads(saved.report_context_json) == {"new": "content"}


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-005")
@pytest.mark.requirement("L3-STAGE-008")
async def test_omitting_email_body_on_retry_clears_it(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    """Omitted email_body_context on retry SHALL null out the persisted column."""
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(
        state=StageState.SUBMITTED,
        submitted_at=_T0,
        email_body_context_json='{"prior":"body"}',
    )

    await use_case.execute(_valid_cmd(email_body_context=None))

    saved: Stage = stage_repo.save.call_args.args[0]
    assert saved.email_body_context_json is None


# -----------------------------------------------------------------------------
# Empty / null context handling (L3-STAGE-010, L3-STAGE-011)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-010")
async def test_empty_report_context_stored_as_empty_json_object(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    """Empty-dict report_context SHALL be persisted as '{}'."""
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run()
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd(report_context={}, email_body_context=None))

    saved: Stage = stage_repo.save.call_args.args[0]
    assert saved.report_context_json == "{}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-STAGE-011")
async def test_both_contexts_omitted_stored_as_null(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run()
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    result = await use_case.execute(_valid_cmd(report_context=None, email_body_context=None))

    assert result.stage_state == StageState.SUBMITTED
    saved: Stage = stage_repo.save.call_args.args[0]
    assert saved.report_context_json is None
    assert saved.email_body_context_json is None


# -----------------------------------------------------------------------------
# Validation precedence (L2-STAGE-009: RUN_NOT_FOUND before UNKNOWN_STAGE)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-009")
@pytest.mark.requirement("L3-STAGE-016")
async def test_run_not_found_raises_before_stage_check(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    """RunNotFoundError takes precedence over UnknownStageError."""
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.side_effect = RunNotFoundError("run not found", details={"run_id": _RID})

    # Even though stage_id is invalid, the run check runs first.
    with pytest.raises(RunNotFoundError):
        await use_case.execute(_valid_cmd(stage_id=_SID_UNKNOWN))

    # Stage lookup never happens.
    stage_repo.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-008")
@pytest.mark.requirement("L3-STAGE-014")
@pytest.mark.requirement("L3-STAGE-015")
async def test_unknown_stage_raises_after_run_lookup(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    with pytest.raises(UnknownStageError) as exc_info:
        await use_case.execute(_valid_cmd(stage_id=_SID_UNKNOWN))

    # L3-STAGE-015: details SHALL include `stage_id` (offending) and
    # `declared_stages` (the run's declared stage ids).
    details = exc_info.value.details
    assert details["stage_id"] == _SID_UNKNOWN
    assert set(details["declared_stages"]) == {_SID_EXTRACT, _SID_TRANSFORM}
    # L3-STAGE-014: declared-stage lookup uses the run aggregate's
    # `declared_stage_ids` property (sourced from `declared_stages_json`);
    # the stage repo SHALL NOT be queried for unknown stage_ids.
    stage_repo.get.assert_not_called()


# -----------------------------------------------------------------------------
# Run terminal state rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-006")
@pytest.mark.parametrize(
    "terminal_state",
    [RunState.SENT, RunState.FAILED, RunState.ORPHANED],
)
async def test_submission_to_terminal_run_raises(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock],
    terminal_state: RunState,
) -> None:
    _, _, run_repo, stage_repo, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run(state=terminal_state)

    with pytest.raises(InvalidRunStateError) as exc_info:
        await use_case.execute(_valid_cmd())

    assert exc_info.value.details["run_state"] == terminal_state.value
    # Stage is never queried, nothing persisted.
    stage_repo.get.assert_not_called()
    stage_repo.save.assert_not_called()
    audit_log.record.assert_not_called()


# -----------------------------------------------------------------------------
# Stage terminal state rejection
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-STAGE-002")
@pytest.mark.parametrize(
    "terminal_state",
    [StageState.ACCEPTED, StageState.TIMEOUT, StageState.FAILED],
)
async def test_submission_to_terminal_stage_raises(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, Any],
    terminal_state: StageState,
) -> None:
    _, _, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(state=terminal_state, submitted_at=_T0)

    with pytest.raises(InvalidStageStateError) as exc_info:
        await use_case.execute(_valid_cmd())

    assert exc_info.value.details["stage_state"] == terminal_state.value
    stage_repo.save.assert_not_called()


# -----------------------------------------------------------------------------
# Run-id well-formedness
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-003")
async def test_malformed_run_id_raises_before_any_port_call(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    factory, _, run_repo, stage_repo, audit_log = uow_bundle

    with pytest.raises(MalformedRequestError):
        await use_case.execute(_valid_cmd(run_id="not-a-uuid"))

    factory.assert_not_called()
    run_repo.get.assert_not_called()
    stage_repo.get.assert_not_called()
    audit_log.record.assert_not_called()


# -----------------------------------------------------------------------------
# Audit-first ordering (L3-RUN-026)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
async def test_audit_recorded_before_stage_save(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    _, _uow, run_repo, stage_repo, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run()
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    manager = MagicMock()
    manager.attach_mock(audit_log.record, "audit_record")
    manager.attach_mock(stage_repo.save, "stage_save")
    manager.attach_mock(run_repo.update_state, "run_update")

    await use_case.execute(_valid_cmd())

    method_calls = [c[0] for c in manager.mock_calls]
    first_audit = method_calls.index("audit_record")
    first_stage = method_calls.index("stage_save")
    assert first_audit < first_stage, f"audit must precede stage save: {method_calls}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
@pytest.mark.requirement("L3-OBS-026")
@pytest.mark.requirement("L3-OBS-029")
async def test_audit_event_captures_retry_metadata(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    _, _, run_repo, stage_repo, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)
    stage_repo.get.return_value = _sample_stage(state=StageState.SUBMITTED, submitted_at=_T0)

    await use_case.execute(_valid_cmd())

    audit_log.record.assert_awaited_once()
    event = audit_log.record.call_args.args[0]
    assert event.action == AuditAction.SUBMIT_STAGE_REPORT
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.details["was_retry"] is True
    assert event.details["prior_stage_state"] == "SUBMITTED"
    assert event.details["new_stage_state"] == "RETRIED"
    assert event.details["run_transitioned_to_aggregating"] is False


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
async def test_audit_event_captures_first_submission_metadata(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, AsyncMock, AsyncMock],
) -> None:
    _, _, run_repo, stage_repo, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.INITIATED)
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd())

    event = audit_log.record.call_args.args[0]
    assert event.details["was_retry"] is False
    assert event.details["prior_stage_state"] == "PENDING"
    assert event.details["new_stage_state"] == "SUBMITTED"
    assert event.details["run_transitioned_to_aggregating"] is True


# -----------------------------------------------------------------------------
# UoW lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_uow_entered_and_exited_once(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, Any],
) -> None:
    factory, uow, run_repo, stage_repo, _ = uow_bundle
    run_repo.get.return_value = _sample_run()
    stage_repo.get.return_value = _sample_stage(state=StageState.PENDING)

    await use_case.execute(_valid_cmd())

    factory.assert_called_once()
    uow.__aenter__.assert_awaited_once()
    uow.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_uow_entered_even_when_run_not_found(
    use_case: SubmitStageReportUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any],
) -> None:
    """Run existence is verified inside the UoW (we need repo access)."""
    _, uow, run_repo, _, _ = uow_bundle
    run_repo.get.side_effect = RunNotFoundError("not found", details={"run_id": _RID})

    with pytest.raises(RunNotFoundError):
        await use_case.execute(_valid_cmd())

    uow.__aenter__.assert_awaited_once()
    # __aexit__ receives the exception and the UoW rolls back.
    uow.__aexit__.assert_awaited_once()
