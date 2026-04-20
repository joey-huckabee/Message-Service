"""Port: persistence for :class:`~message_service.domain.stage.Stage` records.

Stages live in ``(run_id, stage_id)`` composite-key rows. The port
mirrors the :class:`~message_service.application.ports.run_repository.RunRepository`
hybrid style: :meth:`save` for aggregate writes,
:meth:`update_state` for pure state-column transitions.

Submissions use :meth:`save` because they may change multiple columns
at once (state, report_context, email_body_context, submitted_at).

Requirement references
----------------------
L1-PERS-003, L2-STAGE-003, L2-STAGE-004
L3-STAGE-002, L3-STAGE-005, L3-STAGE-006, L3-STAGE-007
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from message_service.domain.aggregates.stage import Stage
from message_service.domain.ids import RunId, StageId
from message_service.domain.state_machines.stage_states import StageState


class StageRepository(ABC):
    """Abstract repository for :class:`Stage` records.

    Implementations MUST:

    * Upsert on the ``(run_id, stage_id)`` unique index (L3-STAGE-005,
      L3-STAGE-006): the same stage submitting twice overwrites in
      place; prior content is not retained by the stage row itself.
      Retry history is captured in the audit log (L3-STAGE-007).
    * Raise
      :class:`~message_service.domain.errors.RunNotFoundError` when a
      stage operation references an unknown run.
    * Raise
      :class:`~message_service.domain.errors.UnknownStageError` when a
      stage operation references a known run but an unknown stage_id.
    """

    @abstractmethod
    async def save(self, stage: Stage) -> None:
        """Insert or overwrite a stage row.

        Uses ``INSERT ... ON CONFLICT(run_id, stage_id) DO UPDATE``
        semantics (L3-STAGE-006). Prior ``report_context_json`` and
        ``email_body_context_json`` are overwritten in place.

        Args:
            stage: The aggregate to persist.

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def get(self, run_id: RunId, stage_id: StageId) -> Stage:
        """Load a single stage by composite key.

        Args:
            run_id: Parent run.
            stage_id: Stage identifier within the run.

        Returns:
            The :class:`Stage` aggregate.

        Raises:
            UnknownStageError: No such stage for this run.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_by_run(self, run_id: RunId) -> Sequence[Stage]:
        """Return every stage belonging to ``run_id``.

        Args:
            run_id: Parent run. Order is unspecified.

        Returns:
            Sequence of stages. Empty if the run has zero declared
            stages (L3-RUN-015 permits this).

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def update_state(
        self,
        run_id: RunId,
        stage_id: StageId,
        new_state: StageState,
        now: datetime,
    ) -> None:
        """Transition a single stage's state column.

        Used by the orphan sweeper to batch-set ``PENDING`` stages to
        ``TIMEOUT``. Does NOT re-read the aggregate; callers MUST have
        validated the transition against
        :func:`~message_service.domain.state_machines.stage_states.transition`.

        Args:
            run_id: Parent run.
            stage_id: Stage to update.
            new_state: Target state.
            now: UTC timestamp for any ``updated_at``-style column the
                adapter tracks.

        Raises:
            UnknownStageError: No such stage.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_pending_by_run(self, run_id: RunId) -> Sequence[StageId]:
        """Return the ids of stages in :attr:`StageState.PENDING` for this run.

        Used by the orphan sweeper to assemble ``pending_stages`` for
        the audit record (L3-STAGE-013).

        Args:
            run_id: Parent run.

        Returns:
            Sequence of stage ids, possibly empty. Order is
            unspecified.

        Raises:
            PersistenceError: Infrastructure failure.
        """


__all__ = ["StageRepository"]
