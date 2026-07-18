"""Input and result DTOs for :class:`SubmitStageReportUseCase`.

The input DTO (:class:`SubmitStageReportCommand`) translates the
``SubmitStageReportRequest`` proto message into a typed, validated
command. The gRPC servicer constructs it from the incoming proto; a
REST or CLI adapter would construct it equivalently.

The result DTO (:class:`SubmitStageReportResult`) returns the stage's
post-submission state plus a ``was_retry`` flag so the gRPC servicer
can surface retry metrics and log retry-rate.

Requirement references
----------------------
L1-STAGE-002 (idempotent on (run_id, stage_id))
L2-STAGE-004 (retry replaces prior content, transitions to RETRIED)
L2-STAGE-006 (empty submission accepted)
L2-AGGR-003 (email body position enum)
L3-STAGE-010 (empty Struct stored as "{}")
L3-STAGE-011 (both contributions omitted stored as null/null)
L3-AGGR-004 (UNSPECIFIED resolved before the command), L3-AGGR-018 (pairing)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from message_service.domain.aggregates.email_body_position import EmailBodyPosition
from message_service.domain.state_machines.stage_states import StageState


class SubmitStageReportCommand(BaseModel):
    """The use case's validated input command.

    Attributes:
        run_id: Target run identifier, as a canonical-form string.
            Well-formedness is checked in the servicer via
            :func:`~message_service.domain.ids.validate_run_id_str`;
            existence is checked by the use case against the
            repository.
        stage_id: Stage identifier, unique within ``run_id``. Must match
            an entry in the run's ``declared_stages`` (L1-STAGE-004).
        report_context: Optional context dict for the stage's report
            template. A distinction with three meanings:

            * ``None`` — the submitter did not include a report
              contribution (L3-STAGE-011).
            * ``{}`` — the submitter included an explicitly-empty
              contribution (L3-STAGE-010); stored as ``"{}"``.
            * non-empty dict — normal submission content.

        email_body_context: Optional context dict for the email body
            template. Same three-way distinction as ``report_context``.
            Per L2-STAGE-005: omitting this on retry explicitly CLEARS
            any previously-recorded email body content.
        email_body_position: Resolved placement of the email body
            contribution relative to the run-level summary block
            (L2-AGGR-003). Set iff ``email_body_context`` is set; the
            gRPC boundary resolves the proto ``UNSPECIFIED`` sentinel to
            ``AFTER_STAGES_SUMMARY`` before constructing the command
            (L3-AGGR-004), so the domain never sees ``UNSPECIFIED``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    run_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    report_context: dict[str, Any] | None = None
    email_body_context: dict[str, Any] | None = None
    email_body_position: EmailBodyPosition | None = None

    @model_validator(mode="after")
    def _check_position_pairing(self) -> SubmitStageReportCommand:
        """Enforce L3-AGGR-018's pairing at the command boundary.

        ``email_body_position`` is present iff ``email_body_context`` is
        present. Catching the mismatch here yields a clear boundary error
        rather than surfacing deep in the :class:`Stage` aggregate's
        ``__post_init__``.
        """
        if (self.email_body_position is None) != (self.email_body_context is None):
            raise ValueError(
                "email_body_position must be set iff email_body_context is set "
                f"(position={self.email_body_position!r}, "
                f"context={'None' if self.email_body_context is None else 'present'})"
            )
        return self


class SubmitStageReportResult(BaseModel):
    """The use case's structured return value.

    Attributes:
        stage_state: The stage's state after the submission. Exactly
            one of ``SUBMITTED`` or ``RETRIED`` on the success path.
        was_retry: ``True`` iff this submission superseded a prior
            submission (i.e. the stage was previously ``SUBMITTED`` or
            ``RETRIED`` before this call). ``False`` on the first
            submission for this ``(run_id, stage_id)``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage_state: StageState
    was_retry: bool


__all__ = ["SubmitStageReportCommand", "SubmitStageReportResult"]
