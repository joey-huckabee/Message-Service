"""Use case: orphan sweeper.

One :meth:`tick` corresponds to one polling iteration of the sweeper
loop. The infrastructure layer owns the periodic scheduling
(``asyncio.sleep`` + loop structure); this use case is the atomic
business-logic unit that's called on each tick.

Per-tick logic:

1. Compute the cutoff timestamp: ``clock.now() - run_timeout``.
2. Query the run repository for runs in active states with their
   last transition older than the cutoff.
3. For each orphaned run, inside a single UoW per run:

   a. Load the run (already returned from the query, but re-read
      inside the UoW to avoid write-write contention with a
      concurrent request-handler transaction).
   b. Transition state to ``ORPHANED``.
   c. Record a ``SWEEP_ORPHAN`` audit event.
   d. Commit.

4. After the commit, dispatch to each registered disposition handler
   whose identifier appears in the configured policy list, in
   config-file order (L2-SWEEP-009).

Handler failures are logged but do not roll back the ORPHANED
transition — once committed, the run IS orphaned regardless of
whether notifications succeed.

L2-SWEEP-006 requires transition + audit to be atomic: they share
a single UoW, so either both persist or neither does.

Requirement references
----------------------
L1-SWEEP-001, L1-SWEEP-002, L1-SWEEP-003
L2-SWEEP-004, L2-SWEEP-005, L2-SWEEP-006, L2-SWEEP-008, L2-SWEEP-009
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import (
    InvalidStateTransitionError,
    RunNotFoundError,
)
from message_service.domain.state_machines.run_states import (
    NON_TERMINAL_STATES,
    RunState,
)
from message_service.domain.state_machines.run_states import (
    transition as transition_run_state,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.disposition_handler import (
        DispositionHandler,
    )
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.config.schema import DispositionAction
    from message_service.domain.aggregates.run import Run
    from message_service.domain.ids import RunId

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TickResult:
    """Outcome of a single :meth:`SweeperUseCase.tick`.

    Used by the infrastructure loop to update Prometheus counters
    with the outcome label required by L2-SWEEP-003.

    Attributes:
        orphaned_count: Number of runs transitioned to ORPHANED this
            tick. Zero when no orphans were found.
        dispatched_actions: Total disposition-handler invocations
            across all orphaned runs. Equals
            ``orphaned_count * len(disposition_actions)`` in the
            nominal case; lower if any handlers raised.
        handler_failures: Count of disposition-handler invocations
            that raised. Useful for alerting.
    """

    orphaned_count: int
    dispatched_actions: int
    handler_failures: int


class SweeperUseCase:
    """The orphan sweeper's per-tick business logic."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        run_timeout_seconds: int,
        disposition_actions: Sequence[DispositionAction],
        handlers_by_id: Mapping[DispositionAction, DispositionHandler],
    ) -> None:
        """Construct a sweeper use case bound to its collaborators.

        Args:
            uow_factory: UoW factory to produce a fresh transaction
                per orphaned run.
            clock: Clock port for ``now()``.
            run_timeout_seconds: Grace window; runs whose last
                transition is older than this are candidates for
                sweeping. From
                ``config.sweeper.run_timeout_seconds``.
            disposition_actions: Ordered list of action identifiers
                from ``config.sweeper.disposition_actions``. Determines
                which handlers run per orphan, and in what order
                (L2-SWEEP-009).
            handlers_by_id: Mapping from action identifier to its
                implementation. MUST contain at least every id that
                appears in ``disposition_actions``; extra entries are
                harmless. Typically one-to-one populated by the
                bootstrap.

        Raises:
            ValueError: ``disposition_actions`` contains an identifier
                for which no handler is registered.
        """
        missing = [a for a in disposition_actions if a not in handlers_by_id]
        if missing:
            raise ValueError(f"no handler registered for disposition action(s): {missing}")
        self._uow_factory = uow_factory
        self._clock = clock
        self._run_timeout = timedelta(seconds=run_timeout_seconds)
        self._disposition_actions = tuple(disposition_actions)
        self._handlers = dict(handlers_by_id)

    async def tick(self) -> TickResult:
        """Run one polling iteration.

        Returns:
            :class:`TickResult` summarizing the work done.

        Raises:
            PersistenceError: A DB-level failure in either the
                list_expired query or one of the per-run UoWs.
                The loop wrapper should log and continue rather
                than crash the entire sweeper.
        """
        now = self._clock.now()
        cutoff = now - self._run_timeout

        # Active states per L2-SWEEP-005 — everything non-terminal.
        active = frozenset(NON_TERMINAL_STATES)

        async with self._uow_factory() as uow:
            candidates: Sequence[Run] = await uow.run_repo.list_expired(
                cutoff=cutoff,
                active_states=active,
            )

        if not candidates:
            _log.debug("sweeper_tick_no_orphans")
            return TickResult(
                orphaned_count=0,
                dispatched_actions=0,
                handler_failures=0,
            )

        _log.info(
            "sweeper_tick_found_orphans",
            count=len(candidates),
            cutoff=cutoff.isoformat(),
        )

        orphaned_count = 0
        dispatched_actions = 0
        handler_failures = 0

        for candidate in candidates:
            committed = await self._transition_and_audit(candidate.run_id)
            if not committed:
                # Either the run was no longer present, or a concurrent
                # transaction finalized it first. Either way nothing
                # to do.
                continue
            orphaned_count += 1

            # Dispatch AFTER commit (L2-SWEEP-006's atomic transition
            # contract covers the ORPHANED write; handler invocations
            # are best-effort beyond that boundary).
            d, f = await self._dispatch_handlers(candidate)
            dispatched_actions += d
            handler_failures += f

        return TickResult(
            orphaned_count=orphaned_count,
            dispatched_actions=dispatched_actions,
            handler_failures=handler_failures,
        )

    # -- Helpers ------------------------------------------------------------

    async def _transition_and_audit(self, run_id: RunId) -> bool:
        """Transition ``run_id`` to ORPHANED + record audit in one UoW.

        Returns ``True`` if the transition committed, ``False`` if the
        run was not in an eligible state (concurrent finalizer won,
        or the run disappeared entirely).
        """
        async with self._uow_factory() as uow:
            try:
                run = await uow.run_repo.get(run_id)
            except RunNotFoundError:
                # Run vanished between list_expired and this read.
                # Nothing to do.
                _log.warning("sweeper_run_gone_before_orphan", run_id=run_id)
                return False

            now = self._clock.now()
            prior_state = run.state

            try:
                next_state = transition_run_state(
                    from_state=prior_state,
                    to_state=RunState.ORPHANED,
                    run_id=run_id,
                )
            except InvalidStateTransitionError:
                # The run raced to a terminal state before we got here.
                _log.info(
                    "sweeper_skipped_raced_to_terminal",
                    run_id=run_id,
                    current_state=prior_state.value,
                )
                return False

            await uow.run_repo.update_state(run_id, next_state, now)

            audit_event = AuditEvent(
                timestamp=now,
                action=AuditAction.SWEEP_ORPHAN,
                actor="system:sweeper",
                resource=f"run:{run_id}",
                outcome=AuditOutcome.SUCCESS,
                details={
                    "run_id": str(run_id),
                    "prior_state": prior_state.value,
                    "new_state": next_state.value,
                    "last_transition_at": run.updated_at.isoformat(),
                },
            )
            await uow.audit_log.record(audit_event)
            return True

    async def _dispatch_handlers(self, run: Run) -> tuple[int, int]:
        """Invoke each configured handler in order.

        Returns ``(dispatched_count, failure_count)``. A handler that
        raises counts toward the failure total but does not stop
        dispatch of subsequent handlers — each action is independent.
        """
        dispatched = 0
        failures = 0

        for action_id in self._disposition_actions:
            handler = self._handlers[action_id]
            try:
                await handler.handle(run)
                dispatched += 1
            except Exception as exc:
                failures += 1
                _log.error(
                    "sweeper_disposition_handler_failed",
                    run_id=str(run.run_id),
                    action=action_id,
                    error=str(exc),
                    exc_info=True,
                )
        return dispatched, failures


__all__ = ["SweeperUseCase", "TickResult"]
