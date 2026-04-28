"""Use case: audit-log retention pruner.

One :meth:`run_once` corresponds to one polling iteration of the
audit-pruner loop. The infrastructure layer owns the periodic
scheduling (``BackgroundTaskScheduler`` cadence); this use case
is the atomic business-logic unit called per tick.

Per-tick logic:

1. Compute cutoff: ``clock.now() - timedelta(days=retention_days)``
   (per L3-OBS-015).
2. In one UoW, execute a bounded ``DELETE FROM audit_log`` against
   rows whose ``timestamp < cutoff``, capped at ``cleanup_batch_size``
   rows per L3-OBS-016. Returns the number of rows deleted.
3. Log the outcome at INFO when rows were deleted, DEBUG otherwise
   (per L3-OBS-040 — empty-tick noise reduction).
4. **NO audit row is emitted for the prune action itself** per
   L3-OBS-040 — recording each prune as an audit row would create
   a self-referential growth pattern (the rows recording past
   prunes would themselves accumulate and need pruning). The
   pruner's structured INFO log is the operational signal;
   forensic-grade auditability of the prune action is provided by
   the L3-OBS-039 sole-deleter conformance test which guarantees
   the pruner is the only DELETE source against ``audit_log``.

Bounded-DELETE mechanic: implemented at the port boundary as
:meth:`AuditLog.delete_older_than`. The SQLite adapter uses a
correlated sub-select on the primary key because stdlib sqlite3
lacks ``SQLITE_ENABLE_UPDATE_DELETE_LIMIT``. Per-tick work is
O(batch_size + log N) on the timestamp index; backlogs larger
than ``cleanup_batch_size`` drain across multiple ticks at the
configured ``cleanup_interval_hours`` cadence (per L3-OBS-016's
"avoid long-running deletes blocking other writers" rationale).

Concurrency: every UoW the pruner opens flows through the
injected ``SqliteUnitOfWorkFactory``; the L2-PERS-004 ``asyncio.Lock``
serializes pruner UoWs against gRPC + sweeper + report-pruner UoWs
automatically. The use case introduces no additional concurrency
primitives.

Sole-deleter invariant (L3-OBS-039): this module is the only
caller in ``src/`` permitted to issue ``DELETE`` or ``UPDATE``
against the ``audit_log`` table. A conformance test enforces this
via SQL string-scan over ``src/`` — see Increment 30e.

Requirement references
----------------------
L1-OBS-003 (append-only audit log + configurable retention)
L2-OBS-008, L2-OBS-009 (retention enforcement + asyncio scheduling)
L3-OBS-014 (24h cadence default)
L3-OBS-015 (DELETE FROM audit_log WHERE timestamp < cutoff)
L3-OBS-016 (per-tick batch ceiling)
L3-OBS-017 (asyncio.create_task + cancellation pattern; via
    BackgroundTaskScheduler in the v1 implementation)
L3-OBS-039 (sole-deleter conformance)
L3-OBS-040 (anti-recursion: no audit row for the prune action)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from message_service.application.ports.clock import iso_z

if TYPE_CHECKING:
    from collections.abc import Callable

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.unit_of_work import UnitOfWork

_log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AuditPruneResult:
    """Outcome of one :meth:`AuditLogPrunerUseCase.run_once` invocation.

    Attributes:
        rows_deleted: Number of audit rows deleted this tick. Capped
            at ``cleanup_batch_size`` per L3-OBS-016. Backlogs larger
            than the cap drain across multiple ticks.
    """

    rows_deleted: int


class AuditLogPrunerUseCase:
    """The audit-log retention pruner's per-tick business logic."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        retention_days: int,
        cleanup_batch_size: int = 10_000,
    ) -> None:
        """Construct a pruner bound to its collaborators.

        Args:
            uow_factory: UoW factory; pruner UoWs serialize through
                the L2-PERS-004 mutex without additional primitives.
            clock: ``Clock`` port for ``now()``. The injected clock
                makes deterministic testing possible (FakeClock can
                advance time to push audit rows past the cutoff).
            retention_days: Minimum days an audit row SHALL be
                retained before the pruner is permitted to delete it
                (per L3-OBS-015 with the strict-less-than boundary).
                Sourced from
                ``config.observability.audit.retention_days``.
            cleanup_batch_size: Per-tick DELETE row cap (per
                L3-OBS-016). Sourced from
                ``config.observability.audit.cleanup_batch_size``.

        Raises:
            ValueError: ``retention_days`` or ``cleanup_batch_size``
                is not positive.
        """
        if retention_days < 1:
            raise ValueError(f"retention_days must be positive; got {retention_days}")
        if cleanup_batch_size < 1:
            raise ValueError(f"cleanup_batch_size must be positive; got {cleanup_batch_size}")
        self._uow_factory = uow_factory
        self._clock = clock
        self._retention = timedelta(days=retention_days)
        self._retention_days = retention_days
        self._batch_size = cleanup_batch_size

    async def run_once(self) -> AuditPruneResult:
        """Run one polling iteration.

        Returns:
            :class:`AuditPruneResult` with the row count deleted.

        Raises:
            PersistenceError: A DB-level failure during the DELETE.
                The loop wrapper should log and continue rather
                than crash the pruner — the next tick will retry
                naturally.
        """
        now = self._clock.now()
        cutoff = now - self._retention
        cutoff_iso = iso_z(cutoff)

        async with self._uow_factory() as uow:
            rows_deleted = await uow.audit_log.delete_older_than(
                cutoff,
                batch_size=self._batch_size,
            )

        if rows_deleted > 0:
            _log.info(
                "audit_log_pruner_tick_completed",
                rows_deleted=rows_deleted,
                retention_days=self._retention_days,
                cutoff_iso_z=cutoff_iso,
            )
        else:
            _log.debug(
                "audit_log_pruner_tick_completed",
                rows_deleted=0,
                retention_days=self._retention_days,
                cutoff_iso_z=cutoff_iso,
            )

        return AuditPruneResult(rows_deleted=rows_deleted)


__all__ = ["AuditLogPrunerUseCase", "AuditPruneResult"]
