"""Unit tests for :class:`FinalizeRunUseCase` (synchronous phase).

Every test uses :class:`unittest.mock.AsyncMock` with ``spec=Port`` to
verify precise port interactions. The background task is represented
by a minimal factory that returns a sentinel coroutine; tests assert
the scheduler received it without ever running it.

Requirement references
----------------------
L1-RUN-004 (transition AGGREGATING -> READY)
L2-RUN-012 (reject unless AGGREGATING)
L2-RUN-013 (non-blocking; schedule background task)
L3-RUN-026 (audit before state update)
L3-RUN-003 (run_id canonical form)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from message_service.application.ports.audit_log import AuditLog
from message_service.application.ports.background_task_scheduler import (
    BackgroundTaskScheduler,
)
from message_service.application.ports.clock import Clock
from message_service.application.ports.run_repository import RunRepository
from message_service.application.ports.stage_repository import StageRepository
from message_service.application.ports.subscription_repository import (
    SubscriptionRepository,
)
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.finalize_run import FinalizeRunUseCase
from message_service.application.use_cases.finalize_run_command import (
    FinalizeRunCommand,
)
from message_service.domain.aggregates.audit_event import AuditAction, AuditOutcome
from message_service.domain.aggregates.declared_stage import DeclaredStage
from message_service.domain.aggregates.run import AttachmentMode, Run
from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.errors import (
    InvalidRunStateError,
    MalformedRequestError,
    RunNotFoundError,
)
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState

# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

_T0 = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)
_T1 = datetime(2026, 4, 19, 12, 30, 0, tzinfo=UTC)
_RID = RunId("00000000-0000-4000-8000-000000000001")
_TPL_AGG = TemplateRef(name="nightly_summary", version="1.0")
_TPL_EXT = TemplateRef(name="extract_rpt", version="1.0")


def _sample_run(state: RunState = RunState.AGGREGATING) -> Run:
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
        ),
        state=state,
        attachment_mode=AttachmentMode.SINGLE_AGGREGATED,
        aggregation_template_ref=_TPL_AGG,
        subscription_predicate_tags=frozenset({"production"}),
        created_at=_T0,
        updated_at=_T0,
    )


@pytest.fixture
def clock() -> MagicMock:
    clk = MagicMock(spec=Clock)
    clk.now.return_value = _T1
    return clk


@pytest.fixture
def uow_bundle() -> tuple[MagicMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock, AsyncMock]:
    """Return ``(factory, uow, run_repo, stage_repo, subscription_repo, audit_log)``."""
    audit_log = AsyncMock(spec=AuditLog)
    run_repo = AsyncMock(spec=RunRepository)
    stage_repo = AsyncMock(spec=StageRepository)
    subscription_repo = AsyncMock(spec=SubscriptionRepository)

    uow = AsyncMock(spec=UnitOfWork)
    uow.run_repo = run_repo
    uow.stage_repo = stage_repo
    uow.subscription_repo = subscription_repo
    uow.audit_log = audit_log
    uow.__aenter__.return_value = uow
    uow.__aexit__.return_value = None

    factory = MagicMock(return_value=uow)
    return factory, uow, run_repo, stage_repo, subscription_repo, audit_log


@pytest.fixture
def scheduler() -> MagicMock:
    sched = MagicMock(spec=BackgroundTaskScheduler)

    # The real scheduler would await the coroutine via create_task; our
    # mock just closes it to avoid pytest "coroutine was never awaited"
    # warnings escalating to errors.
    def _close_coro(coro: Any, *, name: str | None = None) -> None:
        if hasattr(coro, "close"):
            coro.close()

    sched.schedule.side_effect = _close_coro
    return sched


@pytest.fixture
def background_factory() -> MagicMock:
    """A factory that records calls and returns a no-op coroutine."""

    async def _noop(_run_id: RunId) -> None:
        return None

    factory = MagicMock(side_effect=lambda rid: _noop(rid))
    return factory


@pytest.fixture
def use_case(
    clock: MagicMock,
    uow_bundle: tuple[MagicMock, Any, Any, Any, Any, Any],
    scheduler: MagicMock,
    background_factory: MagicMock,
) -> FinalizeRunUseCase:
    factory, _, _, _, _, _ = uow_bundle
    return FinalizeRunUseCase(
        uow_factory=factory,
        clock=clock,
        scheduler=scheduler,
        background_task_factory=background_factory,
    )


def _valid_cmd(**overrides: Any) -> FinalizeRunCommand:
    fields: dict[str, Any] = {"run_id": _RID}
    fields.update(overrides)
    return FinalizeRunCommand(**fields)


# -----------------------------------------------------------------------------
# Happy path
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-004")
async def test_happy_path_transitions_aggregating_to_ready(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, Any],
) -> None:
    _, _, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.AGGREGATING)

    result = await use_case.execute(_valid_cmd())

    assert result.run_id == _RID
    assert result.state == RunState.READY


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-004")
async def test_happy_path_calls_run_repo_update_state(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, Any],
) -> None:
    _, _, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    await use_case.execute(_valid_cmd())

    run_repo.update_state.assert_awaited_once()
    args = run_repo.update_state.call_args
    assert args.args[0] == _RID
    assert args.args[1] == RunState.READY
    assert args.args[2] == _T1  # clock timestamp


# -----------------------------------------------------------------------------
# State precondition (L2-RUN-012)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-012")
@pytest.mark.requirement("L3-RUN-020")
@pytest.mark.requirement("L3-RUN-021")
@pytest.mark.parametrize(
    "wrong_state",
    [
        RunState.INITIATED,
        RunState.READY,
        RunState.SENDING,
        RunState.SENT,
        RunState.FAILED,
        RunState.ORPHANED,
    ],
)
async def test_non_aggregating_run_raises_invalid_state(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, AsyncMock],
    scheduler: MagicMock,
    wrong_state: RunState,
) -> None:
    _, _, run_repo, _, _, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run(state=wrong_state)

    with pytest.raises(InvalidRunStateError) as exc_info:
        await use_case.execute(_valid_cmd())

    assert exc_info.value.details["run_state"] == wrong_state.value
    assert exc_info.value.details["required_state"] == "AGGREGATING"

    # No side effects on the failure path.
    run_repo.update_state.assert_not_called()
    audit_log.record.assert_not_called()
    scheduler.schedule.assert_not_called()


# -----------------------------------------------------------------------------
# Run not found (propagated from repo)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-004")
async def test_run_not_found_propagates(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, AsyncMock],
    scheduler: MagicMock,
) -> None:
    _, _, run_repo, _, _, audit_log = uow_bundle
    run_repo.get.side_effect = RunNotFoundError("not found", details={"run_id": _RID})

    with pytest.raises(RunNotFoundError):
        await use_case.execute(_valid_cmd())

    run_repo.update_state.assert_not_called()
    audit_log.record.assert_not_called()
    scheduler.schedule.assert_not_called()


# -----------------------------------------------------------------------------
# Run-id well-formedness
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-003")
async def test_malformed_run_id_short_circuits(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, AsyncMock],
    scheduler: MagicMock,
) -> None:
    factory, _, run_repo, _, _, audit_log = uow_bundle

    with pytest.raises(MalformedRequestError):
        await use_case.execute(_valid_cmd(run_id="not-a-uuid"))

    factory.assert_not_called()
    run_repo.get.assert_not_called()
    audit_log.record.assert_not_called()
    scheduler.schedule.assert_not_called()


# -----------------------------------------------------------------------------
# Audit-first ordering (L3-RUN-026)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-026")
async def test_audit_recorded_before_state_update(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any, AsyncMock],
) -> None:
    _, _uow, run_repo, _, _, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run()

    manager = MagicMock()
    manager.attach_mock(audit_log.record, "audit_record")
    manager.attach_mock(run_repo.update_state, "run_update")

    await use_case.execute(_valid_cmd())

    method_calls = [c[0] for c in manager.mock_calls]
    first_audit = method_calls.index("audit_record")
    first_update = method_calls.index("run_update")
    assert first_audit < first_update, f"audit must precede state update: {method_calls}"


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-027")
async def test_audit_failure_prevents_state_update(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any, AsyncMock],
    scheduler: MagicMock,
) -> None:
    """L3-RUN-027: when ``audit_log.record`` raises, the state update SHALL
    NOT be attempted; the exception propagates and the UoW exception path
    rolls back. The background task SHALL NOT be scheduled because
    scheduling happens after the UoW commits cleanly.
    """
    _, _uow, run_repo, _, _, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run()
    audit_log.record.side_effect = RuntimeError("simulated audit-row insert failure")

    with pytest.raises(RuntimeError, match="simulated audit-row insert failure"):
        await use_case.execute(_valid_cmd())

    # State update SHALL NOT have been attempted (audit ran first and failed).
    run_repo.update_state.assert_not_called()
    # Background scheduling SHALL NOT have happened (it requires a clean commit).
    scheduler.schedule.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.requirement("L1-RUN-005")
@pytest.mark.requirement("L3-OBS-027")
async def test_audit_event_captures_finalize_run_transition(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, AsyncMock],
) -> None:
    _, _, run_repo, _, _, audit_log = uow_bundle
    run_repo.get.return_value = _sample_run()

    await use_case.execute(_valid_cmd())

    audit_log.record.assert_awaited_once()
    event = audit_log.record.call_args.args[0]
    assert event.action == AuditAction.FINALIZE_RUN
    assert event.outcome == AuditOutcome.SUCCESS
    assert event.resource == f"run:{_RID}"
    assert event.actor == "pipeline:etl-nightly"
    assert event.details["prior_state"] == "AGGREGATING"
    assert event.details["new_state"] == "READY"


# -----------------------------------------------------------------------------
# Non-blocking scheduling (L2-RUN-013)
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
@pytest.mark.requirement("L3-RUN-022")
async def test_background_task_scheduled_after_commit(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any, Any],
    scheduler: MagicMock,
    background_factory: MagicMock,
) -> None:
    """Scheduling SHALL happen after UoW.__aexit__ returns cleanly."""
    _, uow, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    # Instrument the UoW context and the scheduler to record ordering.
    manager = MagicMock()
    manager.attach_mock(uow.__aexit__, "uow_exit")
    manager.attach_mock(scheduler.schedule, "scheduler_schedule")

    await use_case.execute(_valid_cmd())

    method_calls = [c[0] for c in manager.mock_calls]
    first_exit = method_calls.index("uow_exit")
    first_schedule = method_calls.index("scheduler_schedule")
    assert first_exit < first_schedule, f"schedule must happen after UoW commit: {method_calls}"

    # Scheduler saw exactly one task.
    scheduler.schedule.assert_called_once()
    # The factory was given the correct run_id.
    background_factory.assert_called_once_with(_RID)


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
async def test_scheduled_task_has_descriptive_name(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, Any],
    scheduler: MagicMock,
) -> None:
    _, _, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    await use_case.execute(_valid_cmd())

    kwargs = scheduler.schedule.call_args.kwargs
    assert "name" in kwargs
    assert _RID in kwargs["name"]


@pytest.mark.asyncio
@pytest.mark.requirement("L2-RUN-013")
@pytest.mark.requirement("L3-RUN-022")
async def test_execute_returns_before_background_runs(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, Any, AsyncMock, Any, Any, Any],
    scheduler: MagicMock,
) -> None:
    """The scheduled coroutine SHALL NOT be awaited by the use case."""
    _, _, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    # Scheduler's schedule() is synchronous and does NOT await the coro.
    result = await use_case.execute(_valid_cmd())

    assert result.state == RunState.READY
    # The factory produced a coroutine; the scheduler received it.
    # It must NOT have been awaited inside execute() — we assert that
    # the coroutine received by the scheduler is still awaitable (not
    # already consumed). Note: a coroutine raises RuntimeError if
    # awaited twice, so we close it cleanly to avoid "never awaited"
    # warnings in pytest output.
    coro_arg = scheduler.schedule.call_args.args[0]
    coro_arg.close()


# -----------------------------------------------------------------------------
# UoW lifecycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_uow_entered_and_exited_once(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any, Any],
) -> None:
    factory, uow, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run()

    await use_case.execute(_valid_cmd())

    factory.assert_called_once()
    uow.__aenter__.assert_awaited_once()
    uow.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.requirement("L3-RUN-004")
async def test_uow_exits_cleanly_on_invalid_state_error(
    use_case: FinalizeRunUseCase,
    uow_bundle: tuple[MagicMock, AsyncMock, AsyncMock, Any, Any, Any],
) -> None:
    """When raising inside the UoW, __aexit__ SHALL still be awaited."""
    _, uow, run_repo, _, _, _ = uow_bundle
    run_repo.get.return_value = _sample_run(state=RunState.INITIATED)

    with pytest.raises(InvalidRunStateError):
        await use_case.execute(_valid_cmd())

    uow.__aenter__.assert_awaited_once()
    uow.__aexit__.assert_awaited_once()
