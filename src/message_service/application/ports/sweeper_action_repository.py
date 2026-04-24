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


__all__ = ["ClaimedAction", "SweeperActionRepository"]
