"""Port: outbox writes for sweeper-orphaned dispositions.

The orphan sweeper does not invoke disposition handlers in-tick. It
inserts one row per configured action into ``sweeper_actions`` inside
the same transaction as the ORPHANED state transition (L2-SWEEP-006,
L3-SWEEP-010). A separate dispatcher loop later claims pending rows
and runs the corresponding handler.

The split gives the system its exactly-once contract: a crash anywhere
between enqueue and dispatch leaves a recoverable, append-only
"to-do" record on disk.

For 14b.2 the port exposes only :meth:`enqueue` — the write side that
participates in the orphan transaction. The claim/complete/fail
surface used by the dispatcher arrives in 14b.3.

Requirement references
----------------------
L1-SWEEP-002 (orphan sweeper)
L2-SWEEP-006 (atomic transition + enqueue)
L3-SWEEP-010 (sweeper_actions outbox table)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from message_service.config.schema import DispositionAction
    from message_service.domain.ids import RunId


class SweeperActionRepository(ABC):
    """Abstract write-side outbox for sweeper-driven dispositions."""

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


__all__ = ["SweeperActionRepository"]
