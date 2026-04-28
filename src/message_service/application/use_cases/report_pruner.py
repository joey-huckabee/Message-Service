"""Use case: rendered-report retention pruner.

One :meth:`run_once` corresponds to one polling iteration of the
pruner loop. The infrastructure layer owns the periodic scheduling
(``BackgroundTaskScheduler`` cadence); this use case is the atomic
business-logic unit called per tick.

Per-tick logic:

1. Compute cutoff: ``clock.now() - timedelta(days=retention_days)``
   (per L3-PERS-028).
2. In one UoW, query the run repository for runs in terminal states
   (``SENT`` / ``FAILED`` / ``ORPHANED``) whose ``updated_at <= cutoff``,
   capped at ``max_prunes_per_iteration``.
3. For each candidate run:

   a. Walk ``<report_directory>/<run_id>/`` recursively to enumerate
      files (sorted for deterministic order).
   b. Per L3-PERS-031, if processing this run's files would exceed the
      remaining per-tick budget, stop without starting it. The run
      stays a candidate next tick.
   c. For each file in the run's directory, evict it: capture
      ``stat()`` size, ``unlink()``, then open a per-file UoW and
      record one ``PRUNE_REPORT`` audit event (per L3-PERS-033 on
      success, per L3-PERS-034 on failure). Per-file failures
      (``FileNotFoundError``, ``PermissionError``, generic ``OSError``)
      are caught at this boundary, logged at WARNING, and recorded as
      ``outcome=FAILURE``; the pruner continues to the next file
      rather than aborting the tick (mirrors L3-SWEEP-013's
      swallow-with-log pattern).
   d. After all per-file work for the run, ``rmdir`` empty
      subdirectories under the run directory (deepest-first), then the
      run directory itself. Directory cleanup is structural
      housekeeping and is NOT audited (L3-PERS-033 audits files, not
      directories); ``rmdir`` failures are logged at WARNING but do
      not raise.

Concurrency: every UoW the pruner opens is produced by the same
``SqliteUnitOfWorkFactory`` as the gRPC handlers, the orphan sweeper,
and the audit-log pruner; UoW openings serialize through the
L2-PERS-004 ``asyncio.Lock`` automatically. The pruner itself
introduces no additional concurrency primitives (L3-PERS-032).

Sole-deleter invariant: the pruner is the only code path in
``src/`` permitted to call ``Path.unlink()``, ``Path.rmdir()``,
``shutil.rmtree()``, or ``os.remove()`` against paths constructed
under ``persistence.filesystem.report_directory``. This is enforced
by an AST-scan conformance test per L3-PERS-035.

Requirement references
----------------------
L1-PERS-004 (rendered-report retention)
L2-PERS-011, L2-PERS-012, L2-PERS-013
L3-PERS-027, L3-PERS-028, L3-PERS-029, L3-PERS-030, L3-PERS-031
L3-PERS-032, L3-PERS-033, L3-PERS-034, L3-PERS-035
L3-SWEEP-013 (swallow-with-log pattern referenced for failure isolation)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from message_service.application.ports.clock import iso_z
from message_service.domain.aggregates.audit_event import (
    AuditAction,
    AuditEvent,
    AuditOutcome,
)
from message_service.domain.state_machines.run_states import TERMINAL_STATES

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from message_service.application.ports.clock import Clock
    from message_service.application.ports.unit_of_work import UnitOfWork
    from message_service.domain.aggregates.run import Run

_log = structlog.get_logger(__name__)


# SQL-side candidate-fetch ceiling. Generous for the v1 workload (single
# node; modest run rate times90-day retention horizon is well under this).
# A future migration adding ``runs.report_pruned_at`` would let the run
# repository pre-filter already-pruned candidates and obsolete this
# scan-and-skip approach.
_CANDIDATE_QUERY_LIMIT: int = 10_000


@dataclass(frozen=True, slots=True)
class PruneResult:
    """Outcome of one :meth:`ReportPrunerUseCase.run_once` invocation.

    Attributes:
        runs_processed: Number of candidate runs the pruner fully
            processed this tick (file walk + audit + rmdir cleanup).
        files_deleted: Number of files successfully evicted (i.e.,
            ``unlink`` succeeded and the per-file audit row was
            committed).
        files_failed: Number of files for which eviction or audit
            recording failed (per L3-PERS-034). The pruner continued
            to the next file rather than aborting; the failure rows
            are present in the audit log with ``outcome=FAILURE``.
    """

    runs_processed: int
    files_deleted: int
    files_failed: int


class ReportPrunerUseCase:
    """The rendered-report pruner's per-tick business logic."""

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        clock: Clock,
        report_directory: Path,
        retention_days: int,
        max_prunes_per_iteration: int = 1_000,
    ) -> None:
        """Construct a pruner bound to its collaborators.

        Args:
            uow_factory: UoW factory; pruner UoWs serialize through
                the L2-PERS-004 mutex without additional primitives
                (L3-PERS-032).
            clock: ``Clock`` port for ``now()``. The injected clock
                makes deterministic testing possible (FakeClock can
                advance time to push terminal runs past the cutoff).
            report_directory: Root of the on-disk report layout
                (``persistence.filesystem.report_directory``).
            retention_days: Minimum days a rendered report SHALL be
                retained before the pruner is permitted to evict it
                (per L3-PERS-027).
            max_prunes_per_iteration: Per-tick file-deletion cap
                (per L3-PERS-029 / L3-PERS-031). Backlogs larger
                than this drain across multiple ticks at the
                configured polling cadence.

        Raises:
            ValueError: ``retention_days`` or
                ``max_prunes_per_iteration`` is not positive.
        """
        if retention_days < 1:
            raise ValueError(f"retention_days must be positive; got {retention_days}")
        if max_prunes_per_iteration < 1:
            raise ValueError(
                f"max_prunes_per_iteration must be positive; got {max_prunes_per_iteration}"
            )
        self._uow_factory = uow_factory
        self._clock = clock
        self._report_directory = report_directory
        self._retention = timedelta(days=retention_days)
        self._max_prunes = max_prunes_per_iteration

    async def run_once(self) -> PruneResult:
        """Run one polling iteration.

        Returns:
            :class:`PruneResult` summarizing the work done.

        Raises:
            PersistenceError: A DB-level failure in the candidate
                query or one of the per-file audit UoWs. The loop
                wrapper should log and continue rather than crash
                the pruner — the failed iteration's progress is
                already audited.
        """
        now = self._clock.now()
        cutoff = now - self._retention

        async with self._uow_factory() as uow:
            # NB: parameter is named ``active_states`` for historical
            # reasons (the sweeper passes non-terminal states); the
            # contract just filters on the state set, so passing
            # terminal states works correctly per L3-PERS-028.
            #
            # NB on ``limit``: per L3-PERS-031 the per-tick *file*
            # deletion cap is ``max_prunes_per_iteration``, not a cap
            # on the SQL query. We pass a large constant here to give
            # the pruner room to skip past already-pruned candidates
            # (whose Run rows linger in the DB after their files have
            # been evicted) and reach freshly-eligible runs in the
            # same tick. 10 000 is generous for the v1 single-node
            # workload (annual terminal-run rate timesretention horizon
            # is well under this for any realistic deployment); a
            # future migration adding ``runs.report_pruned_at`` would
            # let list_expired pre-filter and remove this scan
            # overhead entirely (potential R-PERS entry).
            candidates: Sequence[Run] = await uow.run_repo.list_expired(
                cutoff=cutoff,
                active_states=TERMINAL_STATES,
                limit=_CANDIDATE_QUERY_LIMIT,
            )

        files_deleted = 0
        files_failed = 0
        runs_processed = 0
        budget_remaining = self._max_prunes

        for candidate in candidates:
            run_dir = self._report_directory / str(candidate.run_id)
            files = self._list_files(run_dir)

            if not files:
                # Already-pruned candidate (or one that never had any
                # report files on disk): cheap rmdir cleanup if a
                # subtree somehow lingers, then move on without
                # consuming budget or incrementing runs_processed.
                # ``runs_processed`` is the number of runs whose files
                # the pruner actually attempted to evict this tick,
                # not the number of DB candidates it iterated.
                self._rmdir_empty_subtree(run_dir)
                continue

            # L3-PERS-031: don't start a run that would exceed the
            # per-tick file-deletion budget. The run stays a candidate
            # for the next tick. Skipping rather than partial-processing
            # keeps run-level deletion atomicity.
            if len(files) > budget_remaining:
                break

            for file_path in files:
                ok = await self._evict_file(
                    file_path=file_path,
                    run=candidate,
                    audit_timestamp=now,
                )
                if ok:
                    files_deleted += 1
                else:
                    files_failed += 1
                budget_remaining -= 1

            self._rmdir_empty_subtree(run_dir)
            runs_processed += 1

        if runs_processed > 0:
            _log.info(
                "report_pruner_tick_completed",
                runs_processed=runs_processed,
                files_deleted=files_deleted,
                files_failed=files_failed,
                budget_remaining=budget_remaining,
            )

        return PruneResult(
            runs_processed=runs_processed,
            files_deleted=files_deleted,
            files_failed=files_failed,
        )

    def _list_files(self, run_dir: Path) -> list[Path]:
        """Return files under ``run_dir`` recursively, sorted.

        Sorted ordering makes the per-tick deletion sequence
        deterministic, which makes integration-test assertions
        stable across platforms.
        """
        if not run_dir.exists():
            return []
        return sorted(p for p in run_dir.rglob("*") if p.is_file())

    def _rmdir_empty_subtree(self, run_dir: Path) -> None:
        """Remove ``run_dir`` and any empty subdirectories under it.

        Walks deepest-first. Failures are logged at WARNING but do not
        raise; a non-empty directory left behind is a benign anomaly
        the operator can clean up, while the audit log already records
        every evicted file (which is the L1-PERS-004 contract).
        """
        if not run_dir.exists():
            return
        subdirs = sorted(
            (p for p in run_dir.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for subdir in subdirs:
            try:
                subdir.rmdir()
            except OSError as exc:
                _log.warning(
                    "report_pruner_rmdir_failed",
                    run_id=run_dir.name,
                    path=str(subdir),
                    error_message=str(exc),
                )
                # Bail on the rest of the cleanup — the parent dir
                # cannot be removed if any child is non-empty.
                return
        try:
            run_dir.rmdir()
        except OSError as exc:
            _log.warning(
                "report_pruner_rmdir_failed",
                run_id=run_dir.name,
                path=str(run_dir),
                error_message=str(exc),
            )

    async def _evict_file(
        self,
        *,
        file_path: Path,
        run: Run,
        audit_timestamp: datetime,
    ) -> bool:
        """Delete one file and record one audit row.

        Per L3-PERS-033 ordering: capture size, unlink, then audit
        in a per-file UoW. Per L3-PERS-034: per-file failures are
        caught and recorded as ``outcome=FAILURE``; the caller
        continues to the next file.

        Args:
            file_path: The file to delete.
            run: The run aggregate whose report this file belongs to;
                used to populate the audit ``details``.
            audit_timestamp: The wall-clock time captured at tick
                start, used as the audit row's ``timestamp`` so
                events recorded inside the same tick share a stable
                ordering anchor.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        size_bytes: int | None = None
        try:
            size_bytes = file_path.stat().st_size
        except OSError as exc:
            _log.warning(
                "report_pruner_stat_failed",
                run_id=str(run.run_id),
                file_path=str(file_path),
                error_message=str(exc),
            )

        try:
            file_path.unlink()
        except FileNotFoundError as exc:
            return await self._record_failure(
                file_path=file_path,
                run=run,
                audit_timestamp=audit_timestamp,
                size_bytes=size_bytes,
                exc=exc,
                event_name="report_pruner_eviction_failed",
            )
        except PermissionError as exc:
            return await self._record_failure(
                file_path=file_path,
                run=run,
                audit_timestamp=audit_timestamp,
                size_bytes=size_bytes,
                exc=exc,
                event_name="report_pruner_eviction_failed",
            )
        except OSError as exc:
            return await self._record_failure(
                file_path=file_path,
                run=run,
                audit_timestamp=audit_timestamp,
                size_bytes=size_bytes,
                exc=exc,
                event_name="report_pruner_eviction_failed",
            )

        # Unlink succeeded; record SUCCESS audit row in its own UoW.
        details: dict[str, object] = {
            "file_path": str(file_path),
            "terminal_state": run.state.value,
            "terminal_state_at": iso_z(run.updated_at),
        }
        if size_bytes is not None:
            details["file_size_bytes"] = size_bytes
        event = AuditEvent(
            timestamp=audit_timestamp,
            action=AuditAction.PRUNE_REPORT,
            actor="system:report_pruner",
            resource=f"report:{run.run_id}",
            outcome=AuditOutcome.SUCCESS,
            details=details,
        )
        try:
            async with self._uow_factory() as uow:
                await uow.audit_log.record(event)
        except Exception as exc:  # noqa: BLE001 — audit-after-effect: file already gone, log loss
            # File deletion succeeded but audit failed. The file is
            # gone; the audit row is not. This matches the
            # L3-PERS-033 documented trade-off (file deletion is not
            # transactional with SQLite). Log the loss at WARNING so
            # operators can find these via log search.
            _log.warning(
                "report_pruner_audit_after_unlink_failed",
                run_id=str(run.run_id),
                file_path=str(file_path),
                error_message=str(exc),
                exc_info=True,
            )
            return False
        return True

    async def _record_failure(
        self,
        *,
        file_path: Path,
        run: Run,
        audit_timestamp: datetime,
        size_bytes: int | None,
        exc: BaseException,
        event_name: str,
    ) -> bool:
        """Log + audit a per-file eviction failure (L3-PERS-034)."""
        _log.warning(
            event_name,
            run_id=str(run.run_id),
            file_path=str(file_path),
            error_message=str(exc),
        )
        details: dict[str, object] = {
            "file_path": str(file_path),
            "terminal_state": run.state.value,
            "terminal_state_at": iso_z(run.updated_at),
            "failure_reason": str(exc),
        }
        if size_bytes is not None:
            details["file_size_bytes"] = size_bytes
        event = AuditEvent(
            timestamp=audit_timestamp,
            action=AuditAction.PRUNE_REPORT,
            actor="system:report_pruner",
            resource=f"report:{run.run_id}",
            outcome=AuditOutcome.FAILURE,
            details=details,
        )
        try:
            async with self._uow_factory() as uow:
                await uow.audit_log.record(event)
        except Exception as audit_exc:  # noqa: BLE001 — best-effort failure audit
            _log.warning(
                "report_pruner_audit_failure_record_failed",
                run_id=str(run.run_id),
                file_path=str(file_path),
                error_message=str(audit_exc),
                exc_info=True,
            )
        return False


__all__ = ["PruneResult", "ReportPrunerUseCase"]
