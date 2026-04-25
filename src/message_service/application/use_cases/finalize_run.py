"""Use case: ``FinalizeRun`` (synchronous phase only).

Called by the pipeline when it has finished submitting stage reports
and wants the service to assemble and deliver the composed email.
Structured as two phases:

**Synchronous phase (this use case)**

1. Load the run; raise :class:`RunNotFoundError` if absent.
2. Validate ``run.state == AGGREGATING``; raise
   :class:`InvalidRunStateError` otherwise (L2-RUN-012).
3. Transition ``AGGREGATING -> READY``.
4. Record audit event.
5. Persist inside a UoW (audit first, then run state update).
6. After successful commit, schedule the background workflow via
   the injected :class:`BackgroundTaskScheduler` (L2-RUN-013).
7. Return a :class:`FinalizeRunResult` without awaiting the workflow.

**Background phase (Increment 7b)**

Implemented separately as ``AssembleAndDeliverUseCase``: transitions
``READY -> SENDING``, loads + sorts stages, renders templates,
resolves recipients, builds the :class:`OutboundEmail`, calls
:meth:`Mailer.send`, and finally transitions to ``SENT`` (on success)
or ``FAILED`` (on unrecoverable delivery failure).

The synchronous phase is decoupled from the background phase via the
``background_task_factory`` parameter: a callable that accepts a
:data:`RunId` and returns a coroutine. This lets 7a be fully built and
tested before 7b exists, and keeps the two use cases swappable at
service composition time.

Scheduling happens AFTER the UoW commits. If scheduling were inside
the UoW and the UoW rolled back, the scheduled task would run against
a state the database never acknowledged. Scheduling after commit
guarantees "if the task runs, the state transition is durable."

Requirement references
----------------------
L1-RUN-004 (FinalizeRun transitions AGGREGATING -> READY)
L1-RUN-005 (record UTC timestamp of every transition)
L2-RUN-012 (reject unless AGGREGATING)
L2-RUN-013 (non-blocking return; asyncio task)
L3-RUN-026 (audit before state change)
L3-RUN-004 (single transaction for state + audit)
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from message_service.application.ports.background_task_scheduler import (
    BackgroundTaskScheduler,
)
from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.finalize_run_command import (
    FinalizeRunCommand,
    FinalizeRunResult,
)
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import InvalidRunStateError
from message_service.domain.ids import RunId, validate_run_id_str
from message_service.domain.state_machines.run_states import (
    RunState,
)
from message_service.domain.state_machines.run_states import (
    transition as transition_run,
)

# Type alias for the coroutine factory that produces the background workflow.
BackgroundTaskFactory = Callable[[RunId], Coroutine[Any, Any, Any]]


class FinalizeRunUseCase:
    """Orchestrator for the synchronous phase of ``FinalizeRun``.

    Dependencies are constructor-injected. Use cases are typically
    constructed once at service start and re-used per request.

    Attributes:
        uow_factory: Zero-argument callable returning a fresh UoW.
        clock: :class:`Clock` port for timestamps.
        scheduler: :class:`BackgroundTaskScheduler` port. The scheduled
            coroutine is produced by :attr:`background_task_factory`.
        background_task_factory: Callable that takes the finalized
            ``run_id`` and returns the coroutine to run in the
            background (typically a bound
            ``AssembleAndDeliverUseCase.execute(run_id)`` coroutine).
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        scheduler: BackgroundTaskScheduler,
        background_task_factory: BackgroundTaskFactory,
        metrics_recorder: MetricsRecorder | None = None,
    ) -> None:
        """Construct with UoW factory, clock, scheduler, and background-task factory.

        Args:
            uow_factory: Zero-argument callable returning a fresh UoW
                per call to :meth:`execute`.
            clock: Port for current UTC timestamp.
            scheduler: Port for enqueuing the background delivery
                workflow.
            background_task_factory: Callable that builds the
                background workflow's coroutine. Kept injectable so
                this use case can be tested in isolation before
                :class:`AssembleAndDeliverUseCase` exists.
            metrics_recorder: L1-OBS-002 metrics port. Defaults to a
                NoOp instance for tests.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._metrics = metrics_recorder or NoOpMetricsRecorder()
        self._scheduler = scheduler
        self._background_task_factory = background_task_factory

    async def execute(self, cmd: FinalizeRunCommand) -> FinalizeRunResult:
        """Transition run to READY and schedule the delivery workflow.

        Args:
            cmd: Validated input command.

        Returns:
            A :class:`FinalizeRunResult` with ``state=RunState.READY``.

        Raises:
            MalformedRequestError: ``cmd.run_id`` is not canonical form.
            RunNotFoundError: No run with ``cmd.run_id`` exists.
            InvalidRunStateError: Run is not currently in
                ``AGGREGATING`` state.
            InvalidStateTransitionError: Defensive — the state-machine
                table disallows the transition. Should not occur given
                the precondition check.
            PersistenceError: Transaction failed; nothing persisted,
                no task scheduled.
        """
        run_id = validate_run_id_str(cmd.run_id)
        now = self._clock.now()

        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)

            # L2-RUN-012: reject unless in AGGREGATING.
            if run.state != RunState.AGGREGATING:
                raise InvalidRunStateError(
                    f"FinalizeRun requires run in AGGREGATING state; "
                    f"run {run_id} is in {run.state.value}",
                    details={
                        "run_id": run_id,
                        "run_state": run.state.value,
                        "required_state": RunState.AGGREGATING.value,
                    },
                )

            # Defense-in-depth: validate via state machine.
            transition_run(
                from_state=run.state,
                to_state=RunState.READY,
                run_id=run_id,
            )

            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.FINALIZE_RUN,
                actor=f"pipeline:{run.pipeline_type}",
                resource=f"run:{run_id}",
                outcome=AuditOutcome.SUCCESS,
                details={
                    "run_id": run_id,
                    "prior_state": RunState.AGGREGATING.value,
                    "new_state": RunState.READY.value,
                    "timestamp": iso_z(now),
                },
            )

            await uow.audit_log.record(audit_event)
            await uow.run_repo.update_state(run_id, RunState.READY, now)

        # L1-OBS-002 / L3-OBS-009: record transition post-commit.
        self._metrics.record_run_state_transition(RunState.READY)

        # UoW has committed. Schedule the background workflow only now;
        # scheduling before commit risks running the workflow against
        # a rolled-back transition.
        coro = self._background_task_factory(run_id)
        self._scheduler.schedule(
            coro,
            name=f"assemble-and-deliver:{run_id}",
        )

        return FinalizeRunResult(run_id=run_id, state=RunState.READY)


__all__ = ["BackgroundTaskFactory", "FinalizeRunUseCase"]
