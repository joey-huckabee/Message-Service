"""Port: append-only audit log for governance and forensics.

The audit log is written transactionally with every state change
(L3-RUN-026): the adapter performs audit ``INSERT`` and the business
``UPDATE`` in the same transaction, with the audit insert first in
statement order. If the audit insert fails, the state change is
aborted; if the state update fails, the audit insert is rolled back.

The port exposes only :meth:`record` and :meth:`query`. There is no
update or delete — retention is handled separately by a background
pruner driven by :attr:`observability.audit.retention_days`
(L1-OBS-003).

Requirement references
----------------------
L1-OBS-003 (audit log scope and retention)
L2-OBS-007 (audit_log table schema), L2-OBS-008 (retention task),
L2-OBS-009 (asyncio cleanup scheduling)
L2-OBS-013, L2-OBS-014, L2-OBS-015, L2-OBS-016, L2-OBS-017
    (per-category audit content rules added in Increment 25b)
L3-RUN-026, L3-RUN-027 (audit-first ordering inside the UoW)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import datetime

from message_service.domain.aggregates.audit_event import AuditAction, AuditEvent


class AuditLog(ABC):
    """Abstract append-only audit log.

    Implementations MUST:

    * Serialize :attr:`AuditEvent.details` as JSON.
    * Preserve the storage order of successive :meth:`record` calls
      within a transaction (callers depend on
      audit-before-state-update ordering, L3-RUN-026).
    * Reject mutation attempts at the schema layer: the audit table
      has no ``UPDATE`` or ``DELETE`` verbs in any adapter code path
      other than the retention pruner.
    """

    @abstractmethod
    async def record(self, event: AuditEvent) -> None:
        """Append one audit event.

        Typically invoked inside the same transaction as the business
        state change the event describes. The adapter is responsible
        for transactional coordination; callers pass the pre-built
        :class:`AuditEvent`.

        Args:
            event: The event to append.

        Raises:
            PersistenceError: Infrastructure failure. The caller's
                enclosing transaction is expected to roll back.
        """

    @abstractmethod
    async def query(
        self,
        *,
        action: AuditAction | None = None,
        resource: str | None = None,
        actor: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
    ) -> Sequence[AuditEvent]:
        """Retrieve audit events matching the filters.

        All filter parameters are ANDed. Unspecified filters are
        not applied. Results are ordered by ``timestamp`` descending
        (most recent first).

        Args:
            action: Exact action match.
            resource: Exact resource string match.
            actor: Exact actor string match.
            since: Inclusive lower bound on ``timestamp``.
            until: Exclusive upper bound on ``timestamp``.
            limit: Maximum number of events to return. MUST be
                positive. Defaults to 1000; callers that need more
                paginate via ``until``.

        Returns:
            Sequence of matching events, most recent first. Empty if
            nothing matches.

        Raises:
            ValueError: If ``limit`` is not positive.
            PersistenceError: Infrastructure failure.
        """


__all__ = ["AuditLog"]
