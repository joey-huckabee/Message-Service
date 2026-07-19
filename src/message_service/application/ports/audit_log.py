"""Port: append-only audit log for governance and forensics.

The audit log is written transactionally with every state change
(L3-RUN-026): the adapter performs audit ``INSERT`` and the business
``UPDATE`` in the same transaction, with the audit insert first in
statement order. If the audit insert fails, the state change is
aborted; if the state update fails, the audit insert is rolled back.

The port exposes :meth:`record` (write), :meth:`query` /
:meth:`list_paginated` (read), and :meth:`delete_older_than`
(retention pruning). The delete method is reserved for the
audit-log retention pruner per L3-OBS-039 (sole-deleter
conformance) — no other caller in ``src/`` is permitted to invoke
it. The pruner is driven by
:attr:`observability.audit.retention_days` and friends (L1-OBS-003 /
L2-OBS-008 / L2-OBS-009).

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

    @abstractmethod
    async def delete_older_than(
        self,
        cutoff: datetime,
        *,
        batch_size: int,
    ) -> int:
        """Delete audit rows whose ``timestamp`` is strictly less than ``cutoff``.

        Reserved for the audit-log retention pruner per L3-OBS-039.
        No other caller in ``src/`` is permitted to invoke this; the
        Increment 30e conformance test enforces the allow-list.

        The boundary is **strict less-than** per L3-OBS-015: a row
        whose ``timestamp`` is exactly equal to ``cutoff`` is
        preserved. (This deliberately differs from L1-SWEEP-002's
        inclusive boundary; each L3 pins its own semantic and the
        spec accepts the inconsistency for v1.)

        Bounded by ``batch_size``: at most that many rows are
        deleted per call. Backlogs larger than ``batch_size`` drain
        across multiple calls. Implementations using SQLite SHALL
        use a sub-select on the primary key to bound the DELETE
        because stdlib sqlite3 lacks
        ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``.

        Args:
            cutoff: Timezone-aware UTC cutoff. Rows with
                ``timestamp < cutoff`` are deleted.
            batch_size: Maximum rows to delete this call. MUST be
                positive.

        Returns:
            Number of rows actually deleted (``cursor.rowcount``).
            Zero when no rows are eligible.

        Raises:
            ValueError: ``batch_size`` is not positive, or
                ``cutoff`` is naive.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def fetch_older_than(
        self,
        cutoff: datetime,
        *,
        batch_size: int,
    ) -> Sequence[AuditEvent]:
        """Return the rows :meth:`delete_older_than` would delete (L3-OBS-042).

        The retention pruner uses this to read the expired batch for
        archival *before* deleting it. It SHALL select exactly the same
        rows :meth:`delete_older_than` removes for the same ``cutoff`` /
        ``batch_size``: the ``timestamp < cutoff`` set, ordered
        ``timestamp ASC, audit_id ASC``, capped at ``batch_size``. Both
        share the ``audit_id`` tiebreak so a batch boundary landing on
        tied timestamps cannot diverge between the two calls.

        This is a read; it imposes no L3-OBS-039 sole-deleter obligation.
        Returned events carry a populated ``audit_id``.

        Args:
            cutoff: Timezone-aware UTC cutoff (strict less-than).
            batch_size: Maximum rows to return. MUST be positive.

        Returns:
            The expired batch in ``(timestamp, audit_id)`` ascending order.

        Raises:
            ValueError: ``batch_size`` is not positive.
            PersistenceError: Infrastructure failure.
        """

    @abstractmethod
    async def list_paginated(
        self,
        *,
        actions: frozenset[AuditAction] | None = None,
        actor: str | None = None,
        resource: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int,
        offset: int,
    ) -> Sequence[AuditEvent]:
        """List audit events for the dashboard viewer (`L1-DASH-005`).

        Differs from :meth:`query` along three axes:

        * ``actions`` is a set (multi-value OR'd), not a single enum.
        * Both ``since`` and ``until`` are **inclusive** bounds (per
          ``L3-DASH-033``) — the historical ``query`` mismatch
          between docstring and implementation does not apply here.
        * Pagination is via explicit ``offset`` + ``limit`` (rather
          than timestamp-windowing), and ordering is by ``audit_id
          DESC`` (per ``L3-DASH-034``) for stability across
          same-timestamp ties — two audit events recorded inside the
          same UoW share an identical timestamp because the use case
          captures ``clock.now()`` once and reuses it.

        Returned events SHALL carry their ``audit_id`` populated.

        Args:
            actions: Optional set of actions; events whose ``action``
                is in the set are included. ``None`` or empty set
                means "no action filter".
            actor: Exact actor string match. ``None`` means no filter.
            resource: Exact resource string match. ``None`` means no
                filter.
            since: Inclusive lower bound on ``timestamp``.
            until: Inclusive upper bound on ``timestamp``.
            limit: Page size. Caller is expected to validate the
                upper bound (route validators cap at 200 per
                ``L3-DASH-033``); the adapter only enforces ``> 0``.
            offset: Number of records to skip. ``>= 0``.

        Returns:
            Sequence of matching events, ``audit_id DESC``. Each
            event's ``audit_id`` is populated.

        Raises:
            ValueError: If ``limit`` is not positive or ``offset`` is
                negative.
            PersistenceError: Infrastructure failure.
        """


__all__ = ["AuditLog"]
