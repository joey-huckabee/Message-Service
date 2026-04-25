"""Port: outbox writes + claims for sweeper-orphaned dispositions.

The orphan sweeper does not invoke disposition handlers in-tick. It
inserts one row per configured action into ``sweeper_actions`` inside
the same transaction as the ORPHANED state transition (L2-SWEEP-006,
L3-SWEEP-010). A separate dispatcher use case
(:class:`~message_service.application.use_cases.sweeper_action_dispatcher.SweeperActionDispatcherUseCase`)
later claims pending rows and runs the corresponding handler.

The split gives the system its exactly-once contract: a crash anywhere
between enqueue and dispatch leaves a recoverable, append-only
"to-do" record on disk.

This port has two halves:

* **Write side (sweeper)** — :meth:`enqueue`, called inside the orphan
  transaction.
* **Claim/settle side (dispatcher)** — :meth:`claim_pending`,
  :meth:`mark_completed`, :meth:`mark_failed`, called by the
  dispatcher per batch.

Requirement references
----------------------
L1-SWEEP-002 (orphan sweeper)
L2-SWEEP-006 (atomic transition + enqueue)
L2-SWEEP-008 (handler registry)
L3-SWEEP-010 (sweeper_actions outbox table)
L3-SWEEP-013 (handlers SHALL NOT raise)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from message_service.config.schema import DispositionAction
    from message_service.domain.ids import RunId


@dataclass(frozen=True, slots=True)
class ClaimedAction:
    """One row claimed by the dispatcher.

    The repository stamps ``claimed_at`` and returns this DTO so the
    dispatcher can invoke the right handler against the right run
    without re-reading the row.

    Attributes:
        action_id: Outbox primary key. Used to settle the row later
            via :meth:`SweeperActionRepository.mark_completed` /
            :meth:`mark_failed`.
        run_id: The orphaned run this action targets.
        action_name: Identifier of the disposition action.
        attempts: Number of prior attempts. ``0`` for first claim;
            higher values indicate re-claims (e.g., stuck-row
            recovery, when added).
    """

    action_id: int
    run_id: RunId
    action_name: DispositionAction
    attempts: int


class SweeperActionRepository(ABC):
    """Abstract outbox for sweeper-driven dispositions."""

    @abstractmethod
    async def enqueue(
        self,
        *,
        run_id: RunId,
        action_name: DispositionAction,
        enqueued_at: datetime,
    ) -> None:
        """Insert one pending row into ``sweeper_actions``.

        Intended to be called inside the same transaction (UoW) as the
        ORPHANED state transition and the ``SWEEP_ORPHAN`` audit
        insert. Either all three persist together or none do.

        Args:
            run_id: The orphaned run.
            action_name: Identifier of the disposition action to run
                later. MUST be one of the values in
                :data:`~message_service.config.schema.DispositionAction`.
                The DB has its own CHECK constraint that mirrors this.
            enqueued_at: Timestamp the row was created. The dispatcher's
                FIFO claim query orders by this value.

        Raises:
            PersistenceError: Infrastructure failure. The caller's
                enclosing transaction is expected to roll back.
        """

    @abstractmethod
    async def claim_pending(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> Sequence[ClaimedAction]:
        """Atomically claim up to ``limit`` oldest pending rows.

        Sets ``claimed_at = now`` on each returned row inside this
        UoW. Subsequent calls within the same tick will not see the
        same rows again (they have ``claimed_at IS NOT NULL``).

        Ordering: ``enqueued_at`` ascending; ties broken by
        ``action_id`` (insert order). This preserves the
        configured-action-order contract from L2-SWEEP-009 within a
        single orphan, since 14b.2 inserts those rows in configured
        order at the same ``enqueued_at`` timestamp.

        Args:
            now: Timestamp to stamp on the claim. Sourced from the
                dispatcher's :class:`Clock`.
            limit: Maximum rows to claim per call. Bounds the
                dispatcher's per-tick work to keep the loop responsive
                under heavy backlogs.

        Returns:
            Claimed rows in claim order. Empty if nothing was
            pending.

        Raises:
            ValueError: ``limit`` is not positive.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def mark_completed(
        self,
        *,
        action_id: int,
        completed_at: datetime,
    ) -> None:
        """Stamp ``completed_at`` on a claimed row after a successful handler run.

        Args:
            action_id: Primary key of the row to settle.
            completed_at: When the handler finished. The DB CHECK
                constraint requires this to be ``>= claimed_at``.

        Raises:
            PersistenceError: Infrastructure failure (the caller's UoW
                should roll back).
        """

    @abstractmethod
    async def mark_failed(
        self,
        *,
        action_id: int,
        completed_at: datetime,
        error_message: str,
    ) -> None:
        """Settle a claimed row whose handler raised.

        Bumps ``attempts``, records ``last_error``, stamps
        ``completed_at``. The row is therefore terminal for this
        attempt — re-running requires a future stuck-claim recovery
        pass that resets ``claimed_at``/``completed_at``.

        Per L3-SWEEP-013 ("handlers SHALL NOT raise — failures are
        logged at ERROR and swallowed"), the dispatcher logs the
        failure, calls this method, and continues with the next row.
        Failure of one action does not affect siblings.

        Args:
            action_id: Primary key of the row to settle.
            completed_at: When the handler returned (with the
                exception). Same CHECK constraint as :meth:`mark_completed`.
            error_message: Human-readable failure reason. Persisted to
                ``last_error`` for forensics; not exposed to handlers.

        Raises:
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def reclaim_stuck(
        self,
        *,
        now: datetime,
        limit: int,
        stale_threshold_seconds: int,
        max_attempts: int,
    ) -> Sequence[ClaimedAction]:
        """Atomically re-claim stuck rows for retry (L3-SWEEP-020).

        A row is "stuck" when its claim has aged past
        ``stale_threshold_seconds`` without ``completed_at`` being
        stamped — typically because the dispatcher process that
        claimed it crashed. Reclaiming bumps ``attempts`` by 1 and
        sets ``claimed_at`` to ``now``, then returns the row so the
        dispatcher can re-invoke the handler.

        Rows whose ``attempts`` already equals ``max_attempts`` SHALL
        NOT be reclaimed (they need abandonment, not another retry —
        see :meth:`find_abandoned`).

        Args:
            now: Timestamp to stamp on the new claim. Sourced from the
                dispatcher's :class:`Clock`.
            limit: Maximum rows to reclaim per call.
            stale_threshold_seconds: A row qualifies if its
                ``claimed_at`` is at least this many seconds before
                ``now``. Default operator value is 300; longer if
                handlers can take >5 min.
            max_attempts: Reclaim cap — rows already at this attempts
                count are not reclaimed.

        Returns:
            Reclaimed rows in claim order. Empty if nothing was stuck.
            ``ClaimedAction.attempts`` reflects the post-bump value.

        Raises:
            ValueError: ``limit`` not positive, ``stale_threshold_seconds``
                or ``max_attempts`` not positive.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def find_abandoned(
        self,
        *,
        now: datetime,
        stale_threshold_seconds: int,
        max_attempts: int,
        limit: int,
    ) -> Sequence[ClaimedAction]:
        """Return stuck rows whose retries are exhausted (L3-SWEEP-021).

        A row qualifies when ``attempts >= max_attempts`` AND it's
        stuck (``completed_at IS NULL`` AND ``claimed_at`` older than
        ``stale_threshold_seconds``).

        Pure-read (does not mutate). The dispatcher follows up with one
        :meth:`mark_abandoned` call per returned row, plus an audit
        ``DISPATCHER_ACTION_ABANDONED`` event so operators can see what
        was given up on.

        Args:
            now: For computing the stale cutoff.
            stale_threshold_seconds: Same threshold as
                :meth:`reclaim_stuck`.
            max_attempts: Same cap as :meth:`reclaim_stuck`. A row
                qualifies as abandoned when its ``attempts >= max_attempts``
                AND it's stuck.
            limit: Maximum rows to return per call. Bounds per-tick
                abandonment audit volume.

        Returns:
            Stuck-and-exhausted rows. Empty if nothing matches.

        Raises:
            ValueError: any param not positive.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def mark_abandoned(
        self,
        *,
        action_id: int,
        completed_at: datetime,
        error_message: str,
    ) -> None:
        """Mark a row as abandoned: terminal failure after retry exhaustion.

        Sets ``completed_at`` and ``last_error``. Distinct from
        :meth:`mark_failed` because:

        * Does NOT bump ``attempts`` (the count already reflects the
          full retry history).
        * Pairs with a ``DISPATCHER_ACTION_ABANDONED`` audit event
          emitted by the dispatcher in the same UoW (L3-SWEEP-021).

        Args:
            action_id: Primary key of the row to abandon.
            completed_at: When the dispatcher gave up. Same CHECK
                constraint as :meth:`mark_completed`.
            error_message: Final failure reason — typically the
                ``last_error`` already in the row, repeated here so
                this UPDATE is self-contained.

        Raises:
            PersistenceError: Infrastructure failure.
        """


__all__ = ["ClaimedAction", "SweeperActionRepository"]
