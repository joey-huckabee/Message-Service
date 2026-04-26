"""Use case: drain the ``sweeper_actions`` outbox.

Pairs with :class:`~message_service.application.use_cases.sweeper.SweeperUseCase`.
The sweeper enqueues one row per configured disposition action inside
the orphan transaction (L2-SWEEP-006); this use case claims the
pending rows, invokes the registered handler against the run aggregate,
and stamps the result on the outbox row.

Per-tick logic:

1. Open a UoW. Claim up to ``batch_limit`` oldest pending rows
   (``claimed_at`` stamped to ``clock.now()``). Load the corresponding
   :class:`Run` aggregates. Commit. After this commit, the rows are
   in-flight: some other consumer (or this same dispatcher on
   restart) will not re-claim them.
2. Outside any UoW: invoke ``handler.handle(run)`` per claimed row.
   Per L3-SWEEP-013, handlers SHALL NOT crash the dispatcher — any
   exception is caught, logged, and translated into a settle-failed
   call. One handler's failure never affects siblings.
3. Open one UoW per row to settle: ``mark_completed`` on success,
   ``mark_failed`` (with ``attempts + 1`` and ``last_error``) on
   failure.

Three UoWs (one for the batch claim, then two per row for settle) is
deliberately granular — a slow handler must not hold a transaction
open across the network call, and per-row settle keeps a slow settle
from blocking sibling settlements.

Crash semantics
---------------
* **Crash in phase 1, after claim commit**: rows are in-flight
  (claimed but not completed). Without a stuck-claim recovery pass —
  not in 14b.3 — these rows are stuck. Future increment.
* **Crash in phase 2 (handler invocation)**: same as above; the
  in-flight row outlives the process. The L2-SWEEP-006 contract that
  matters is held: no row is ever dispatched twice.
* **Crash in phase 3 (settle)**: the handler ran, but ``completed_at``
  was never stamped. Identical observable state to a phase-2 crash.
  Idempotent handlers (the v1 set is log-only) make this benign;
  non-idempotent handlers in future increments must coordinate via
  their own dedup if exact-once delivery is required.

Requirement references
----------------------
L2-SWEEP-006 (exactly-once outbox handoff)
L2-SWEEP-008 (handler registry)
L3-SWEEP-013 (handlers SHALL NOT raise; failures swallowed)
L3-SWEEP-015 (handlers invoked in configured order; preserved by
the claim query's ``enqueued_at, action_id`` ordering)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.errors import RunNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.disposition_handler import (
        DispositionHandler,
    )
    from message_service.application.ports.sweeper_action_repository import (
        ClaimedAction,
    )
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.config.schema import DispositionAction
    from message_service.domain.aggregates.run import Run

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DispatchResult:
    """Outcome of one :meth:`SweeperActionDispatcherUseCase.dispatch_pending`.

    Attributes:
        claimed: Rows the dispatcher successfully claimed this tick.
            Includes both fresh-pending and reclaimed-stuck rows
            (L3-SWEEP-020).
        succeeded: Rows whose handler ran cleanly and were stamped
            ``completed_at``.
        failed: Rows whose handler raised, were caught, and were
            stamped failed (``attempts++``, ``last_error``).
            ``claimed == succeeded + failed`` always.
        abandoned: Rows that exhausted retries (``attempts >=
            max_dispatch_attempts``) and were marked terminal
            without another handler invocation (L3-SWEEP-021). The
            dispatcher emits one ``DISPATCHER_ACTION_ABANDONED`` audit
            event per row so operators can trace giveup decisions.
    """

    claimed: int
    succeeded: int
    failed: int
    abandoned: int = 0


class SweeperActionDispatcherUseCase:
    """Drains the sweeper_actions outbox one batch at a time."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        handlers_by_id: Mapping[DispositionAction, DispositionHandler],
        batch_limit: int = 100,
        stale_claim_threshold_seconds: int = 300,
        max_dispatch_attempts: int = 3,
    ) -> None:
        """Bind the dispatcher to its collaborators.

        Args:
            uow_factory: UoW factory; the dispatcher uses one per
                claim and one per settle.
            clock: ``now()`` for both ``claimed_at`` and
                ``completed_at`` stamps.
            handlers_by_id: Same registry the bootstrap builds for
                :class:`SweeperUseCase`. Rows whose ``action_name`` is
                not in this map are settled as failed with a
                "no_handler_registered" error — that path covers the
                edge case where a config change between enqueue and
                claim removes a handler.
            batch_limit: Maximum rows claimed per tick. Bounds work
                under heavy backlogs so other adapters (loop heartbeat,
                etc.) get airtime.
            stale_claim_threshold_seconds: Stuck-claim recovery
                threshold (L3-SWEEP-020). A row claimed-but-not-
                completed for at least this long is reclaimable.
            max_dispatch_attempts: Cap on stuck-claim retries
                (L3-SWEEP-021). After this many attempts a row is
                abandoned with an audit event.
        """
        if batch_limit < 1:
            raise ValueError(f"batch_limit must be positive; got {batch_limit}")
        if stale_claim_threshold_seconds < 1:
            raise ValueError(
                f"stale_claim_threshold_seconds must be positive; "
                f"got {stale_claim_threshold_seconds}"
            )
        if max_dispatch_attempts < 1:
            raise ValueError(f"max_dispatch_attempts must be positive; got {max_dispatch_attempts}")
        self._uow_factory = uow_factory
        self._clock = clock
        self._handlers = dict(handlers_by_id)
        self._batch_limit = batch_limit
        self._stale_threshold_seconds = stale_claim_threshold_seconds
        self._max_attempts = max_dispatch_attempts

    async def dispatch_pending(self) -> DispatchResult:
        """Run one drain pass.

        Three phases per tick:

        1. **Abandon** stuck rows whose retries are exhausted (one
           UoW; one audit event per row).
        2. **Claim** fresh-pending + reclaim-stuck rows (one UoW
           combined; reclaim bumps ``attempts``).
        3. **Settle** each claimed row in its own UoW
           (mark_completed / mark_failed).

        Returns:
            :class:`DispatchResult` summarizing the work done. The
            sweeper loop logs counts at INFO when ``claimed > 0``.

        Raises:
            PersistenceError: A DB-level failure during any phase.
                The caller (sweeper loop) should log and continue to
                the next tick.
        """
        # Phase 0: abandonment. Detect+settle rows whose stuck retries
        # exhausted before claiming new work, so the abandoned rows
        # don't hold up the per-tick batch budget.
        abandoned_count = await self._abandon_exhausted_rows()

        # Phase 1: claim a batch (fresh + stuck-recovery) + load runs.
        async with self._uow_factory() as uow:
            now = self._clock.now()
            claimed_fresh = await uow.sweeper_action_repo.claim_pending(
                now=now,
                limit=self._batch_limit,
            )
            remaining = self._batch_limit - len(claimed_fresh)
            claimed_stuck: Sequence[ClaimedAction] = []
            if remaining > 0:
                claimed_stuck = await uow.sweeper_action_repo.reclaim_stuck(
                    now=now,
                    limit=remaining,
                    stale_threshold_seconds=self._stale_threshold_seconds,
                    max_attempts=self._max_attempts,
                )
            claimed: list[ClaimedAction] = [*claimed_fresh, *claimed_stuck]

            runs_by_action_id: dict[int, Run | None] = {}
            for c in claimed:
                try:
                    runs_by_action_id[c.action_id] = await uow.run_repo.get(c.run_id)
                except RunNotFoundError:
                    runs_by_action_id[c.action_id] = None

        if not claimed:
            return DispatchResult(claimed=0, succeeded=0, failed=0, abandoned=abandoned_count)

        _log.info(
            "dispatcher_claimed_batch",
            count=len(claimed),
            fresh=len(claimed_fresh),
            reclaimed=len(claimed_stuck),
        )

        # Phase 2: invoke handlers outside any UoW (handlers may issue
        # network calls; we MUST NOT hold a DB transaction across them).
        outcomes: list[tuple[ClaimedAction, bool, str | None]] = []
        for c in claimed:
            run = runs_by_action_id[c.action_id]
            if run is None:
                outcomes.append((c, False, "run no longer exists"))
                _log.error(
                    "dispatcher_run_missing",
                    action_id=c.action_id,
                    run_id=str(c.run_id),
                    action_name=c.action_name,
                )
                continue

            handler = self._handlers.get(c.action_name)
            if handler is None:
                outcomes.append((c, False, f"no handler registered for {c.action_name}"))
                _log.error(
                    "dispatcher_handler_unregistered",
                    action_id=c.action_id,
                    action_name=c.action_name,
                )
                continue

            try:
                await handler.handle(run)
                outcomes.append((c, True, None))
            except Exception as exc:  # noqa: BLE001 — L3-SWEEP-013: per-handler isolation; logged below
                # L3-SWEEP-013: handlers SHALL NOT raise — failures
                # logged at ERROR and swallowed so one failure doesn't
                # block siblings.
                outcomes.append((c, False, str(exc)))
                _log.error(
                    "dispatcher_handler_failed",
                    action_id=c.action_id,
                    run_id=str(c.run_id),
                    action_name=c.action_name,
                    error=str(exc),
                    exc_info=True,
                )

        # Phase 3: settle each row in its own UoW.
        succeeded = 0
        failed = 0
        for c, ok, err in outcomes:
            settle_now = self._clock.now()
            async with self._uow_factory() as uow:
                if ok:
                    await uow.sweeper_action_repo.mark_completed(
                        action_id=c.action_id,
                        completed_at=settle_now,
                    )
                    succeeded += 1
                else:
                    await uow.sweeper_action_repo.mark_failed(
                        action_id=c.action_id,
                        completed_at=settle_now,
                        error_message=err or "unknown",
                    )
                    failed += 1

        return DispatchResult(
            claimed=len(claimed),
            succeeded=succeeded,
            failed=failed,
            abandoned=abandoned_count,
        )

    async def _abandon_exhausted_rows(self) -> int:
        """Find stuck rows whose retries exhausted, audit them, mark them terminal.

        Implements L3-SWEEP-021. Returns the count of abandoned rows.
        Bounded by ``batch_limit`` per tick so a sudden mass
        abandonment doesn't dominate one dispatcher iteration.
        """
        async with self._uow_factory() as uow:
            now = self._clock.now()
            exhausted = await uow.sweeper_action_repo.find_abandoned(
                now=now,
                stale_threshold_seconds=self._stale_threshold_seconds,
                max_attempts=self._max_attempts,
                limit=self._batch_limit,
            )
            for row in exhausted:
                # Audit first per L3-RUN-026's audit-before-state-update
                # convention; the audit + mark_abandoned share this UoW
                # so they commit together.
                audit_event = AuditEvent(
                    timestamp=now,
                    action=AuditAction.DISPATCHER_ACTION_ABANDONED,
                    actor="system:sweeper_action_dispatcher",
                    resource=f"sweeper_action:{row.action_id}",
                    outcome=AuditOutcome.FAILURE,
                    details={
                        "action_id": row.action_id,
                        "run_id": str(row.run_id),
                        "action_name": row.action_name,
                        "attempts": row.attempts,
                        "max_attempts": self._max_attempts,
                    },
                )
                await uow.audit_log.record(audit_event)
                await uow.sweeper_action_repo.mark_abandoned(
                    action_id=row.action_id,
                    completed_at=now,
                    error_message=(
                        f"abandoned after {row.attempts} attempts "
                        f"(max_dispatch_attempts={self._max_attempts})"
                    ),
                )
                _log.warning(
                    "dispatcher_action_abandoned",
                    action_id=row.action_id,
                    run_id=str(row.run_id),
                    action_name=row.action_name,
                    attempts=row.attempts,
                )
        return len(exhausted)


__all__ = ["DispatchResult", "SweeperActionDispatcherUseCase"]
