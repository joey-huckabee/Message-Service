"""Use case: ``SubmitStageReport``.

A pipeline stage calls ``SubmitStageReport`` to submit (or retry) its
contribution to a run's report. The use case:

1. Loads the run and rejects if missing or terminal.
2. Validates the stage_id is declared for this run (L2-STAGE-008).
3. Loads the current stage record to classify first-submission vs retry.
4. Rejects submissions to stages already in a terminal state (ACCEPTED,
   TIMEOUT, FAILED).
5. Computes the next stage state via the stage state machine
   (PENDING→SUBMITTED, SUBMITTED→RETRIED, RETRIED→RETRIED).
6. If the run is still in INITIATED, transitions it to AGGREGATING.
7. Persists in one UoW: audit first (L3-RUN-026), then stage upsert,
   then run state update (if run transitioned).
8. Returns (stage_state, was_retry).

Validation precedence (L2-STAGE-009):

* RUN_NOT_FOUND takes precedence over UNKNOWN_STAGE. The use case
  checks run existence first; only if the run exists does it check
  the stage.

Requirement references
----------------------
L1-STAGE-002 (idempotent on (run_id, stage_id))
L1-STAGE-003 (even empty submission moves out of PENDING)
L1-STAGE-004 (reject unknown stage_id)
L2-STAGE-004, L2-STAGE-005, L2-STAGE-006, L2-STAGE-008, L2-STAGE-009
L3-STAGE-006 (atomic upsert)
L3-STAGE-007 (retry audited separately)
L3-STAGE-010 (empty-Struct → "{}")
L3-STAGE-011 (both omitted → null/null)
L3-RUN-026 (audit before state change)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from message_service.application.ports.clock import Clock, iso_z
from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
from message_service.application.ports.unit_of_work import UnitOfWork
from message_service.application.use_cases.submit_stage_report_command import (
    SubmitStageReportCommand,
    SubmitStageReportResult,
)
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.aggregates.run import Run
from message_service.domain.aggregates.stage import Stage
from message_service.domain.errors import (
    InvalidRunStateError,
    InvalidStageStateError,
    UnknownStageError,
)
from message_service.domain.ids import RunId, StageId, validate_run_id_str
from message_service.domain.state_machines.run_states import (
    TERMINAL_STATES as RUN_TERMINAL_STATES,
)
from message_service.domain.state_machines.run_states import (
    RunState,
)
from message_service.domain.state_machines.run_states import (
    transition as transition_run,
)
from message_service.domain.state_machines.stage_states import (
    TERMINAL_STATES as STAGE_TERMINAL_STATES,
)
from message_service.domain.state_machines.stage_states import (
    StageState,
)
from message_service.domain.state_machines.stage_states import (
    transition as transition_stage,
)

# States from which a stage may be submitted-to. Derived once: the
# complement of terminal plus the reserved-for-v2 IN_PROGRESS state.
_STAGE_SUBMISSION_SOURCES: frozenset[StageState] = frozenset(
    {StageState.PENDING, StageState.SUBMITTED, StageState.RETRIED}
)


def _serialize_context(ctx: dict[str, Any] | None) -> str | None:
    """Serialize a context dict for persistence.

    Args:
        ctx: Context dict or ``None``.

    Returns:
        * ``None`` if ``ctx`` is ``None`` (L3-STAGE-011).
        * ``"{}"`` if ``ctx`` is an empty dict (L3-STAGE-010).
        * JSON-encoded string otherwise. ``sort_keys=True`` produces
          deterministic encoding so equivalent submissions produce
          byte-equal storage (useful for audit/diffing).
    """
    if ctx is None:
        return None
    return json.dumps(ctx, sort_keys=True, separators=(",", ":"))


class SubmitStageReportUseCase:
    """Orchestrator for the ``SubmitStageReport`` gRPC.

    Dependencies (ports) are constructor-injected. Use cases are
    typically constructed once at service start and re-used per
    request.

    Attributes:
        uow_factory: Zero-argument callable returning a fresh UoW.
        clock: :class:`Clock` port for timestamps.
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        metrics_recorder: MetricsRecorder | None = None,
    ) -> None:
        """Construct with UoW factory and clock.

        Args:
            uow_factory: Zero-argument callable returning a fresh UoW
                per call to :meth:`execute`.
            clock: Port for current UTC timestamp.
            metrics_recorder: L1-OBS-002 metrics port. Defaults to
                a NoOp instance for tests.
        """
        self._uow_factory = uow_factory
        self._clock = clock
        self._metrics = metrics_recorder or NoOpMetricsRecorder()

    async def execute(self, cmd: SubmitStageReportCommand) -> SubmitStageReportResult:
        """Persist a stage submission, transitioning run and stage states.

        Args:
            cmd: Validated input command.

        Returns:
            A :class:`SubmitStageReportResult` describing the stage's
            post-submission state and whether this was a retry.

        Raises:
            RunNotFoundError: No run with ``cmd.run_id`` exists. Takes
                precedence over :class:`UnknownStageError` per
                L2-STAGE-009.
            UnknownStageError: The ``stage_id`` is not declared in the
                run's ``declared_stages``, or no matching stage record
                exists (defense-in-depth).
            InvalidRunStateError: The run is in a terminal state
                (SENT, FAILED, ORPHANED) and cannot accept submissions.
            InvalidStageStateError: The stage is in a terminal state
                (ACCEPTED, TIMEOUT, FAILED) and cannot accept further
                submissions.
            MalformedRequestError: ``cmd.run_id`` is not a canonical
                UUID-4 string.
            PersistenceError: Transaction failed; nothing persisted.
        """
        # Validate run_id well-formedness (raises MalformedRequestError).
        run_id: RunId = validate_run_id_str(cmd.run_id)
        stage_id = StageId(cmd.stage_id)

        now = self._clock.now()

        async with self._uow_factory() as uow:
            # ---------------------------------------------------------
            # 1. Load the run. RunNotFoundError takes precedence over
            #    any stage-level error per L2-STAGE-009.
            # ---------------------------------------------------------
            run: Run = await uow.run_repo.get(run_id)

            # ---------------------------------------------------------
            # 2. Reject submissions to terminal runs.
            # ---------------------------------------------------------
            if run.state in RUN_TERMINAL_STATES:
                raise InvalidRunStateError(
                    f"run {run_id} is in terminal state {run.state.value}; "
                    f"submissions are no longer accepted",
                    details={
                        "run_id": run_id,
                        "run_state": run.state.value,
                    },
                )

            # ---------------------------------------------------------
            # 3. Validate stage_id is declared for this run.
            # ---------------------------------------------------------
            if stage_id not in run.declared_stage_ids:
                raise UnknownStageError(
                    f"stage_id {stage_id!r} is not declared for run {run_id}",
                    details={
                        "run_id": run_id,
                        "stage_id": stage_id,
                        "declared_stages": sorted(run.declared_stage_ids),
                    },
                )

            # ---------------------------------------------------------
            # 4. Load the current stage record. Should exist if
            #    BeginRun wired things up correctly; defense-in-depth.
            # ---------------------------------------------------------
            current_stage: Stage = await uow.stage_repo.get(run_id, stage_id)

            # ---------------------------------------------------------
            # 5. Reject submissions to terminal stages.
            # ---------------------------------------------------------
            if current_stage.state in STAGE_TERMINAL_STATES:
                raise InvalidStageStateError(
                    f"stage ({run_id}, {stage_id}) is in terminal state "
                    f"{current_stage.state.value}; further submissions rejected",
                    details={
                        "run_id": run_id,
                        "stage_id": stage_id,
                        "stage_state": current_stage.state.value,
                    },
                )

            # Defensive: the state machine allows only PENDING/SUBMITTED/
            # RETRIED as submission sources; if we see something else
            # (e.g., IN_PROGRESS reserved for v2), reject.
            if current_stage.state not in _STAGE_SUBMISSION_SOURCES:
                raise InvalidStageStateError(
                    f"stage ({run_id}, {stage_id}) in state "
                    f"{current_stage.state.value} does not accept submissions",
                    details={
                        "run_id": run_id,
                        "stage_id": stage_id,
                        "stage_state": current_stage.state.value,
                    },
                )

            # ---------------------------------------------------------
            # 6. Compute transitions.
            # ---------------------------------------------------------
            was_retry = current_stage.state != StageState.PENDING
            if current_stage.state == StageState.PENDING:
                next_stage_state = StageState.SUBMITTED
            else:
                # SUBMITTED → RETRIED or RETRIED → RETRIED
                next_stage_state = StageState.RETRIED

            # Validate via state machine (defense-in-depth; also yields
            # a consistent error object if our logic drifts from the
            # table).
            transition_stage(
                from_state=current_stage.state,
                to_state=next_stage_state,
                run_id=run_id,
                stage_id=stage_id,
            )

            # Run transitions INITIATED → AGGREGATING on first
            # submission for any stage (per state machine). Subsequent
            # submissions while run is AGGREGATING leave it unchanged.
            run_transitioned = run.state == RunState.INITIATED
            if run_transitioned:
                transition_run(
                    from_state=run.state,
                    to_state=RunState.AGGREGATING,
                    run_id=run_id,
                )

            # ---------------------------------------------------------
            # 7. Build the new Stage (replaces the existing row).
            # ---------------------------------------------------------
            # L3-AGGR-018: the stored position is set iff the email body
            # contribution is present. The command guarantees this pairing
            # (its model validator) and carries the position already
            # resolved from the request — UNSPECIFIED became
            # AFTER_STAGES_SUMMARY at the gRPC boundary (L3-AGGR-004).
            new_stage = Stage(
                run_id=run_id,
                stage_id=stage_id,
                state=next_stage_state,
                report_template_ref=current_stage.report_template_ref,
                report_context_json=_serialize_context(cmd.report_context),
                email_body_context_json=_serialize_context(cmd.email_body_context),
                email_body_position=cmd.email_body_position,
                submitted_at=now,
            )

            # ---------------------------------------------------------
            # 8. Build audit event.
            # ---------------------------------------------------------
            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.SUBMIT_STAGE_REPORT,
                actor=f"pipeline:{run.pipeline_type}",
                resource=f"run:{run_id}/stage:{stage_id}",
                outcome=AuditOutcome.SUCCESS,
                details={
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "prior_stage_state": current_stage.state.value,
                    "new_stage_state": next_stage_state.value,
                    "was_retry": was_retry,
                    "run_transitioned_to_aggregating": run_transitioned,
                    "has_report_context": cmd.report_context is not None,
                    "has_email_body_context": cmd.email_body_context is not None,
                    "timestamp": iso_z(now),
                },
            )

            # ---------------------------------------------------------
            # 9. Persist: audit first (L3-RUN-026), then stage, then
            #    run state (if transitioned).
            # ---------------------------------------------------------
            await uow.audit_log.record(audit_event)
            await uow.stage_repo.save(new_stage)
            if run_transitioned:
                await uow.run_repo.update_state(run_id, RunState.AGGREGATING, now)

        # L1-OBS-002 / L3-OBS-009: emit transition metrics post-commit.
        self._metrics.record_stage_state_transition(next_stage_state)
        if run_transitioned:
            self._metrics.record_run_state_transition(RunState.AGGREGATING)

        return SubmitStageReportResult(
            stage_state=next_stage_state,
            was_retry=was_retry,
        )


__all__ = ["SubmitStageReportUseCase"]
