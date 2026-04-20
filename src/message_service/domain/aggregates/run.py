"""The :class:`Run` aggregate: the top-level lifecycle entity for a single pipeline run.

A :class:`Run` represents the request-through-delivery arc for one
pipeline execution:

- Created by ``BeginRun`` (INITIATED → AGGREGATING)
- Collects per-stage submissions (stages reach SUBMITTED/ACCEPTED)
- Finalized by ``FinalizeRun`` (AGGREGATING → READY → SENDING → SENT)
- May end in FAILED or ORPHANED

The :class:`Run` is a value object: frozen, hashable, comparable by
value. Domain operations that would change state (e.g., transitioning
from AGGREGATING to READY) return a new :class:`Run` rather than
mutating. The :class:`RunRepository` port persists whichever version
the use case commits.

Design notes
------------
- Frozen + slots: cheap, immutable, prevents accidental mutation
  elsewhere in the pipeline.
- ``declared_stages`` is a :class:`frozenset` for O(1) membership
  checks during SubmitStageReport validation (L3-RUN-014).
- ``tags`` is a :class:`frozenset` for the same reason during recipient
  resolution (L3-SUB-005).
- Timestamps are timezone-aware UTC :class:`~datetime.datetime`
  instances. Persistence layers convert to ISO-8601-with-"Z" strings
  (L3-RUN-025).
- ``aggregation_template_ref`` and ``attachment_mode`` capture the
  ``BeginRun`` decisions needed at assembly time without re-reading the
  request payload.

Requirement references
----------------------
L2-RUN-001, L2-RUN-002, L2-RUN-003, L2-RUN-011
L3-RUN-001, L3-RUN-002, L3-RUN-014, L3-RUN-025
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from message_service.domain.aggregates.template_ref import TemplateRef
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.run_states import RunState


class AttachmentMode(StrEnum):
    """Delivery mode for a finalized run's rendered output.

    ``SINGLE_AGGREGATED``: all stage contributions compose into one
    composite attachment via the run's ``aggregation_template``.

    ``PER_STAGE``: each stage's ``report_template`` renders independently;
    the email carries one attachment per ACCEPTED stage.
    """

    SINGLE_AGGREGATED = "SINGLE_AGGREGATED"
    PER_STAGE = "PER_STAGE"


@dataclass(frozen=True, slots=True)
class Run:
    """A pipeline run's top-level state.

    Attributes:
        run_id: The canonical UUID string (L3-RUN-002).
        pipeline_type: Registered pipeline type name (L2-RUN-007).
        tags: Controlled-vocabulary tags attached to this run
            (L2-SUB-006). Used for subscription-based recipient
            resolution.
        declared_stages: The stages the pipeline promised to submit.
            Fixed at BeginRun; cannot grow (L3-RUN-015 allows empty).
        state: Current lifecycle state. Mutated only via
            :mod:`~message_service.domain.state_machines.run_states`.
        attachment_mode: Chosen at BeginRun; governs assembly behavior.
        aggregation_template_ref: Required when ``attachment_mode`` is
            :attr:`AttachmentMode.SINGLE_AGGREGATED`; ignored when
            :attr:`AttachmentMode.PER_STAGE` (L3-RUN-018).
        subscription_predicate_tags: Subset of ``tags`` intended for
            tag-based subscription resolution. Currently identical to
            ``tags`` but kept separate so future runs could split
            "metadata tags" from "routing tags" without a schema
            migration.
        created_at: UTC wall-clock at BeginRun (from injected
            :class:`~message_service.application.ports.clock.Clock`).
        updated_at: UTC wall-clock at the most recent state transition.
            Invariant: ``updated_at >= created_at``.
    """

    run_id: RunId
    pipeline_type: str
    tags: frozenset[str]
    declared_stages: frozenset[StageId]
    state: RunState
    attachment_mode: AttachmentMode
    created_at: datetime
    updated_at: datetime
    aggregation_template_ref: TemplateRef | None = None
    subscription_predicate_tags: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate aggregate invariants at construction time.

        Raises:
            ValueError: If any timezone, ordering, or consistency
                invariant is violated.
        """
        if self.created_at.tzinfo is None:
            raise ValueError("Run.created_at must be timezone-aware")
        if self.updated_at.tzinfo is None:
            raise ValueError("Run.updated_at must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError(f"Run.updated_at ({self.updated_at}) < created_at ({self.created_at})")
        if (
            self.attachment_mode is AttachmentMode.SINGLE_AGGREGATED
            and self.aggregation_template_ref is None
        ):
            raise ValueError(
                "Run with attachment_mode=SINGLE_AGGREGATED requires aggregation_template_ref"
            )


__all__ = ["AttachmentMode", "Run"]
