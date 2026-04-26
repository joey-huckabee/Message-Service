"""Use case: ``ListPastRuns`` -- paginated past-runs listing.

Thin orchestrator over :class:`RunRepository.list_paginated`. Most
behavioural detail (validation, query-param shapes) lives at the
route layer; this use case exists so the dashboard surface is
consistent with the bootstrap composition pattern (route ->
use case -> port).

Default state filter: when no states are provided, the use case
restricts to terminal states per `L3-DASH-023`; the route layer
forwards the user's filter when supplied.

Requirement references
----------------------
L1-DASH-003 (past-runs view)
L2-DASH-012 (paginated listing semantics)
L3-DASH-023 (default state filter), L3-DASH-024 (ordering)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from message_service.domain.state_machines.run_states import TERMINAL_STATES

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.run import Run
    from message_service.domain.state_machines.run_states import RunState


class ListPastRunsUseCase:
    """Return up to ``limit`` runs in the requested state set, paginated."""

    def __init__(self, *, uow_factory: Callable[[], UnitOfWork]) -> None:
        """Bind to UoW factory."""
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        limit: int,
        offset: int,
        states: frozenset[RunState] | None = None,
    ) -> Sequence[Run]:
        """List past runs.

        Args:
            limit: Maximum runs to return; the route layer constrains
                this to ``[1, 200]`` per L3-DASH-023.
            offset: Number of rows to skip; the route layer
                constrains this to ``>= 0`` per L3-DASH-023.
            states: State filter. ``None`` defaults to
                :data:`TERMINAL_STATES` per L3-DASH-023.

        Returns:
            A sequence of at most ``limit`` runs, ordered most-recent
            first per L3-DASH-024.
        """
        effective_states = TERMINAL_STATES if states is None else states
        async with self._uow_factory() as uow:
            return await uow.run_repo.list_paginated(
                effective_states,
                limit=limit,
                offset=offset,
            )


__all__ = ["ListPastRunsUseCase"]
