"""Use case: ``GetRunDetail`` -- fetch a single run with its stage list.

Backs the ``GET /runs/{run_id}`` route. Returns the run aggregate
plus the ordered list of :class:`Stage` aggregates for the run; the
route layer projects these into the L3-DASH-026 response shape
(excluding the large per-stage JSON context payloads).

Order of stages: the run aggregate's ``declared_stages`` defines the
canonical order. The repository's ``list_by_run`` returns stages in
some persistence-implementation order; this use case re-orders them
to match ``declared_stages`` so the route response is deterministic.

Requirement references
----------------------
L1-DASH-003 (run-detail view)
L2-DASH-013 (run + ordered stage list)
L3-DASH-025 (route shape and 404 semantics)
L3-DASH-026 (response payload shape)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from message_service.domain.errors import RunNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.run import Run
    from message_service.domain.aggregates.stage import Stage
    from message_service.domain.ids import RunId


@dataclass(frozen=True, slots=True)
class RunDetail:
    """Composite return value for the run-detail use case."""

    run: Run
    stages: tuple[Stage, ...]


class GetRunDetailUseCase:
    """Fetch a run plus its stages in declared-stage order."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Bind to UoW factory."""
        self._uow_factory = uow_factory

    async def execute(self, *, run_id: RunId) -> RunDetail:
        """Return the run + its stages, ordered by ``declared_stages``.

        Args:
            run_id: The run to fetch.

        Returns:
            :class:`RunDetail` carrying the run and its stages in
            declared order.

        Raises:
            RunNotFoundError: No run with ``run_id`` exists.
        """
        async with self._uow_factory() as uow:
            run = await uow.run_repo.get(run_id)  # raises RunNotFoundError
            stages = await uow.stage_repo.list_by_run(run_id)

        # Re-order stages to match declared_stages. The repo's order is
        # implementation-specific (likely insertion order in SQLite,
        # but not contractually guaranteed); the L3-DASH-026 response
        # contract pins declared-stage order so the dashboard list
        # stays deterministic.
        by_id = {stage.stage_id: stage for stage in stages}
        ordered: list[Stage] = []
        for declared in run.declared_stages:
            if declared.stage_id in by_id:
                ordered.append(by_id[declared.stage_id])
        return RunDetail(run=run, stages=tuple(ordered))


__all__ = ["GetRunDetailUseCase", "RunDetail", "RunNotFoundError"]
