"""The :class:`Stage` aggregate: per-stage state and contribution content within a run.

Each stage declared in the :class:`~message_service.domain.run.Run`
aggregate has a corresponding :class:`Stage` record. Stages start in
:attr:`~message_service.domain.state_machines.stage_states.StageState.PENDING`,
transition to
:attr:`~message_service.domain.state_machines.stage_states.StageState.SUBMITTED`
when the pipeline calls ``SubmitStageReport``, and typically settle in
:attr:`~message_service.domain.state_machines.stage_states.StageState.ACCEPTED`
once the service has validated and persisted the contribution.

Each stage may contribute two independent pieces of content:

1. A **report fragment** — context for the stage's report template.
2. An **email body contribution** — context for the email body
   template.

Either may be omitted; both are cleared by passing ``None``.

Design notes
------------
- Frozen + slots; same rationale as :class:`Run`.
- ``report_context_json`` and ``email_body_context_json`` are stored as
  JSON strings (not ``dict``) to match persistence column types
  (L3-STAGE-009) and to avoid accidental mutation of nested dicts by
  callers.
- ``submitted_at`` is ``None`` for stages still in ``PENDING``;
  populated only at first submission (L3-STAGE-007).
- Retries overwrite in place; there is no submission history on the
  stage itself. The audit log (AuditLog port) preserves retry events
  (L3-STAGE-007).

Requirement references
----------------------
L2-STAGE-001, L2-STAGE-003, L2-STAGE-004, L2-STAGE-005
L3-STAGE-002, L3-STAGE-005, L3-STAGE-007, L3-STAGE-008, L3-STAGE-009
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.stage_states import StageState


@dataclass(frozen=True, slots=True)
class Stage:
    """Per-stage state and content within a run.

    Attributes:
        run_id: Parent run. Part of the composite primary key.
        stage_id: Caller-supplied identifier unique within the run.
        state: Current lifecycle state.
        report_template_ref: Template for this stage's report fragment
            (required at BeginRun per L3-RUN-016).
        report_context_json: JSON-encoded context dict for the report
            template. ``None`` when the stage has not submitted or when
            the submitter explicitly cleared the report contribution.
        email_body_context_json: JSON-encoded context dict for the
            email body template. Independent of the report
            contribution: either may be ``None`` while the other is
            populated (L3-STAGE-009).
        submitted_at: UTC timestamp of the most recent successful
            submission. ``None`` while the stage is still ``PENDING``
            (L3-STAGE-007).
    """

    run_id: RunId
    stage_id: StageId
    state: StageState
    report_template_ref: TemplateRef
    report_context_json: str | None = None
    email_body_context_json: str | None = None
    submitted_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate stage invariants.

        Raises:
            ValueError: If ``submitted_at`` is naive, or if the state
                implies content-presence constraints that are not met.
        """
        if self.submitted_at is not None and self.submitted_at.tzinfo is None:
            raise ValueError("Stage.submitted_at must be timezone-aware when set")

        # Stages in the PENDING state have not submitted anything yet.
        if self.state is StageState.PENDING and self.submitted_at is not None:
            raise ValueError(
                f"Stage in PENDING state cannot have a submitted_at timestamp "
                f"(got {self.submitted_at})"
            )

        # Any state reachable only via a submission must carry submitted_at.
        submission_states = {
            StageState.SUBMITTED,
            StageState.ACCEPTED,
            StageState.RETRIED,
        }
        if self.state in submission_states and self.submitted_at is None:
            raise ValueError(f"Stage in {self.state} state must have a submitted_at timestamp")


__all__ = ["Stage"]
