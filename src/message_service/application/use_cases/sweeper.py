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
   d. Insert one ``sweeper_actions`` outbox row per configured
      disposition action (L3-SWEEP-010), in configured order
      (L2-SWEEP-009 / L3-SWEEP-015), inside the same transaction.
   e. Commit.

The handler invocation lives elsewhere: a separate dispatcher loop
(arrives in 14b.3) claims pending outbox rows and runs the registered
:class:`DispositionHandler` for each. That two-phase split is what
gives **L2-SWEEP-006** its exactly-once contract: a crash anywhere
between enqueue and dispatch leaves a recoverable record on disk.

The use case still receives the handlers map at construction so it
can validate at startup that every configured action id has a
registered handler (L3-SWEEP-012); the runtime path uses only the
keys.

Requirement references
----------------------
L1-SWEEP-001, L1-SWEEP-002, L1-SWEEP-003
L2-SWEEP-004, L2-SWEEP-005, L2-SWEEP-006, L2-SWEEP-008, L2-SWEEP-009
L3-SWEEP-009 (atomic transition), L3-SWEEP-010 (outbox enqueue)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from message_service.application.ports.metrics_recorder import (
    MetricsRecorder,
    NoOpMetricsRecorder,
)
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import (
    ConfigurationError,
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
        enqueued_actions: Total ``sweeper_actions`` rows inserted
            across all orphaned runs. Equals
            ``orphaned_count * len(disposition_actions)`` exactly —
            inserts that fail roll back the entire orphan transaction
            for that run, so partial counts cannot occur.
    """

    orphaned_count: int
    enqueued_actions: int


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
        disposition_overrides: Mapping[str, Sequence[DispositionAction]] | None = None,
        max_candidates_per_iteration: int = 1_000,
        metrics_recorder: MetricsRecorder | None = None,
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
                appears in ``disposition_actions`` or any
                ``disposition_overrides`` value; extra entries are
                harmless. Typically one-to-one populated by the
                bootstrap.
            disposition_overrides: Optional per-pipeline disposition policy
                (L2-SWEEP-011) from
                ``config.pipelines.orphan_disposition_overrides``, mapping
                ``pipeline_type`` to its ordered action list. A run whose
                ``pipeline_type`` is a key uses that list; all others use
                ``disposition_actions``. Defaults to no overrides.
            max_candidates_per_iteration: Per-tick cap on orphan
                candidates the sweeper claims (L2-SWEEP-010 /
                L3-SWEEP-008). Backlogs larger than this drain across
                multiple ticks at the configured polling cadence.
                Default 1000 mirrors L3-SWEEP-008's spec.
            metrics_recorder: L1-OBS-002 metrics port. Defaults to
                a NoOp instance for tests.

        Raises:
            ConfigurationError: ``disposition_actions`` or any
                ``disposition_overrides`` value contains an identifier for
                which no handler is registered. Surfaces at bootstrap time so
                misconfiguration fails loud-and-early rather than per-orphan
                at runtime (cf. L3-SWEEP-012 / L3-SWEEP-024).
            ValueError: ``max_candidates_per_iteration`` is not positive.
        """
        if max_candidates_per_iteration < 1:
            raise ValueError(
                f"max_candidates_per_iteration must be positive; got {max_candidates_per_iteration}"
            )
        overrides = {pt: tuple(actions) for pt, actions in (disposition_overrides or {}).items()}
        # L3-SWEEP-024: validate the global policy AND every override action
        # against the registered handlers, in a stable order for diagnostics.
        all_actions = list(disposition_actions) + [
            a for actions in overrides.values() for a in actions
        ]
        missing = sorted({a for a in all_actions if a not in handlers_by_id})
        if missing:
            raise ConfigurationError(
                f"no handler registered for disposition action(s): {missing}",
                details={
                    "missing_actions": missing,
                    "registered_actions": sorted(handlers_by_id.keys()),
                },
            )
        self._uow_factory = uow_factory
        self._clock = clock
        self._run_timeout = timedelta(seconds=run_timeout_seconds)
        self._disposition_actions = tuple(disposition_actions)
        self._disposition_overrides: dict[str, tuple[DispositionAction, ...]] = overrides
        self._handlers = dict(handlers_by_id)
        self._max_candidates = max_candidates_per_iteration
        self._metrics = metrics_recorder or NoOpMetricsRecorder()

    def _resolve_disposition_actions(self, pipeline_type: str) -> tuple[DispositionAction, ...]:
        """Return the disposition action list for a pipeline (L3-SWEEP-023).

        Args:
            pipeline_type: The orphaned run's ``pipeline_type``.

        Returns:
            The per-pipeline override list when configured, else the global
            ``disposition_actions``.
        """
        return self._disposition_overrides.get(pipeline_type, self._disposition_actions)

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
                limit=self._max_candidates,
            )

        if not candidates:
            _log.debug("sweeper_tick_no_orphans")
            return TickResult(orphaned_count=0, enqueued_actions=0)

        _log.info(
            "sweeper_tick_found_orphans",
            count=len(candidates),
            cutoff=cutoff.isoformat(),
        )

        orphaned_count = 0
        enqueued_actions = 0

        for candidate in candidates:
            enqueued = await self._transition_audit_and_enqueue(candidate.run_id)
            if enqueued is None:
                # Either the run was no longer present, or a concurrent
                # transaction finalized it first. Either way nothing
                # to do. (0 is a valid committed count for an empty
                # per-pipeline override, so we test `is None`, not falsiness.)
                continue
            orphaned_count += 1
            enqueued_actions += enqueued

        return TickResult(
            orphaned_count=orphaned_count,
            enqueued_actions=enqueued_actions,
        )

    # -- Helpers ------------------------------------------------------------

    async def _transition_audit_and_enqueue(self, run_id: RunId) -> int | None:
        """Transition + audit + outbox enqueue in one UoW (L2-SWEEP-006).

        Three writes share a single transaction so they commit together
        or roll back together:

        * ``runs.state`` updated to ``ORPHANED``
        * ``audit_log`` row recording ``SWEEP_ORPHAN``
        * One ``sweeper_actions`` row per resolved disposition action

        Returns the number of disposition actions enqueued (>= 0) if the
        transaction committed, or ``None`` if the run was not in an eligible
        state (concurrent finalizer won, or the run disappeared entirely).
        Ineligible cases are unrecoverable for this tick, not errors. The
        return distinguishes ``0`` (committed with an empty per-pipeline
        override) from ``None`` (not committed).
        """
        async with self._uow_factory() as uow:
            try:
                run = await uow.run_repo.get(run_id)
            except RunNotFoundError:
                # Run vanished between list_expired and this read.
                # Nothing to do.
                _log.warning("sweeper_run_gone_before_orphan", run_id=run_id)
                return None

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
                return None

            # L3-SWEEP-023: resolve the disposition action list for this run's
            # pipeline (per-pipeline override else the global policy). The same
            # resolved list drives the audit record, the outbox rows, and the
            # returned count.
            resolved_actions = self._resolve_disposition_actions(run.pipeline_type)

            await uow.run_repo.update_state(run_id, next_state, now)

            # L2-STAGE-007 / L3-SWEEP-020: capture which stages were still
            # PENDING at orphan time. Recorded inside the audit details so
            # operators investigating an incident can see "this run
            # orphaned because stages X and Y never reported." Loaded
            # inside the same UoW to get a consistent snapshot.
            pending_stage_ids = await uow.stage_repo.list_pending_by_run(run_id)

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
                    "enqueued_actions": list(resolved_actions),
                    "pending_stage_ids": sorted(str(sid) for sid in pending_stage_ids),
                },
            )
            await uow.audit_log.record(audit_event)

            # Outbox enqueue, in configured order (L2-SWEEP-009 /
            # L3-SWEEP-015). The dispatcher reads back rows ordered by
            # enqueued_at; same-timestamp rows are then ordered by
            # action_id (auto-increment) which mirrors insert order.
            for action_id in resolved_actions:
                await uow.sweeper_action_repo.enqueue(
                    run_id=run_id,
                    action_name=action_id,
                    enqueued_at=now,
                )

            # Capture for post-commit metric emission. ORPHANED is a
            # terminal state, so the duration histogram closes here.
            duration_seconds = (now - run.created_at).total_seconds()

        # L1-OBS-002 / L3-OBS-009 metrics, post-commit.
        self._metrics.record_run_state_transition(next_state)
        self._metrics.observe_run_duration_seconds(duration_seconds)
        return len(resolved_actions)


__all__ = ["SweeperUseCase", "TickResult"]
