"""A single declared stage within a :class:`~message_service.domain.aggregates.run.Run`.

Declared at BeginRun time, one per stage the pipeline promises to
submit. Carries:

* The stage identifier used on subsequent ``SubmitStageReport`` calls.
* The presentation order — assembly code sorts by this, NOT by
  submission time (L1-RUN-ordering requirement).
* The stage's report template reference — validated at BeginRun but
  used later when rendering the stage's contribution.

Requirement references
----------------------
L1-RUN-003 (BeginRun validates declared stages)
L2-RUN-009 (declared-stages contract)
L3-RUN-014, L3-RUN-015, L3-RUN-016
"""

from __future__ import annotations

from dataclasses import dataclass

from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import StageId


@dataclass(frozen=True, slots=True)
class DeclaredStage:
    """One stage declaration from ``BeginRunRequest.declared_stages``.

    Attributes:
        stage_id: Caller-supplied identifier, unique within the run.
        stage_order: Presentation order for the aggregated attachment
            and email body. Non-negative integer. Multiple stages MAY
            share a ``stage_order`` value; ties break by lexicographic
            ``stage_id`` for determinism.
        report_template_ref: Template to render this stage's report
            fragment. Existence is validated at BeginRun against the
            :class:`~message_service.application.ports.template_repository.TemplateRepository`
            (L3-RUN-016).
    """

    stage_id: StageId
    stage_order: int
    report_template_ref: TemplateRef

    def __post_init__(self) -> None:
        """Validate non-negative stage_order.

        Raises:
            ValueError: If ``stage_order`` is negative.
        """
        if self.stage_order < 0:
            raise ValueError(
                f"DeclaredStage.stage_order must be non-negative (got {self.stage_order})"
            )


__all__ = ["DeclaredStage"]
